"""LogSense and incident → change correlation, on a FIXTURE log stream (synthetic, hand-written)."""

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from hindsight.api import app as app_module
from hindsight.core.db import set_meta
from hindsight.correlate.deploys import candidates, record_deploy
from hindsight.logsense.alerts import send_alert
from hindsight.logsense.collector import read_jsonl
from hindsight.logsense.engine import Engine, service_aliases
from hindsight.logsense.events import is_stream_noise, normalise
from hindsight.logsense.faults import SCENARIOS, inject, revert
from hindsight.logsense.incidents import get_incident

T0 = 1_700_000_000
FAULT_AT = 900
CHARGE_ERROR = (
    "failed to charge card: could not charge the card: rpc error: code = Unavailable desc = connection error:"
    ' desc = "transport: Error while dialing: dial tcp: lookup paymentservice on 10.96.0.10:53: no such host"'
)


def _line(t: float, message: str, severity: str) -> str:
    stamp = datetime.fromtimestamp(T0 + t, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")
    return f"{stamp} " + json.dumps({"message": message, "severity": severity})


def stream(fault: bool, minutes: int = 18) -> list:
    """FIXTURE: healthy shop traffic, then (optionally) paymentservice goes silent at FAULT_AT."""
    lines: list[tuple[str, str]] = []
    for t in range(minutes * 60):
        broken = fault and t >= FAULT_AT
        lines.append(
            ("frontend", _line(t, f"request complete path=/product/{t % 9} status=200", "info"))
        )
        if t % 5 == 0 and not broken:
            lines.append(
                (
                    "checkoutservice",
                    _line(t, f"payment went through (transaction_id: {uuid.uuid4()})", "info"),
                )
            )
            lines.append(
                (
                    "paymentservice",
                    _line(
                        t, f"Transaction processed: visa ending {t % 9000} amount USD {t}", "info"
                    ),
                )
            )
        if broken:
            lines.append(("checkoutservice", _line(t + 0.1, CHARGE_ERROR, "error")))
            lines.append(
                (
                    "frontend",
                    _line(
                        t + 0.2,
                        "failed to complete the order: rpc error: code = Internal desc = "
                        + CHARGE_ERROR,
                        "error",
                    ),
                )
            )
    events = [normalise(line, service) for service, line in lines]
    return sorted((e for e in events if e is not None), key=lambda e: e.ts)


def run_engine(conn, events):
    engine = Engine(conn, service_aliases(conn), warmup=600, source="test")
    for e in events:
        engine.add(e)
    engine.flush()
    return engine


def test_normalise_reads_json_plain_and_numeric_levels():
    e = normalise('2024-01-01T00:00:00.123456789Z {"message": "boom", "severity": "ERROR"}', "svc")
    assert e is not None and e.severity == "error" and e.message == "boom"
    assert abs(e.ts - datetime(2024, 1, 1, tzinfo=UTC).timestamp() - 0.123456) < 1e-3
    plain = normalise("2024-01-01T00:00:01Z connection refused: service unavailable", "svc")
    assert plain is not None and plain.severity == "error"
    pino = normalise('2024-01-01T00:00:02Z {"msg": "slow", "level": 40}', "svc")
    assert pino is not None and pino.severity == "warn"
    assert normalise("", "svc") is None


def test_payment_outage_names_payment_as_culprit_and_others_as_victims(fx):
    conn = fx.conn()
    engine = run_engine(conn, stream(fault=True))
    assert len(engine.closed) == 1
    incident = engine.closed[0]
    assert 0 <= incident.opened_at - (T0 + FAULT_AT) <= 60  # detected within a minute
    roles = {r["service"]: r["role"] for r in incident.data["ranking"]}
    assert roles["paymentservice"] == "culprit"
    assert roles["checkoutservice"] == "victim" and roles["frontend"] == "victim"
    full = get_incident(conn, incident.id)
    assert full is not None and "Likely culprit: paymentservice" in full.summary
    assert full.timeline["frontend"] and any(p.errors for p in full.timeline["frontend"])
    assert all(t.id.startswith("T") for t in full.templates)


def test_healthy_traffic_raises_no_incident(fx):
    engine = run_engine(fx.conn(), stream(fault=False))
    assert engine.closed == []


def test_replay_from_recorded_jsonl_matches_live_processing(fx, tmp_path):
    path = tmp_path / "recorded.jsonl"
    path.write_text("\n".join(e.to_json() for e in reversed(stream(fault=True))))
    events = read_jsonl([path])
    assert events == sorted(events, key=lambda e: e.ts)
    engine = run_engine(fx.conn(), events)
    assert [r["service"] for r in engine.closed[0].data["ranking"] if r["role"] == "culprit"] == [
        "paymentservice"
    ]


def test_correlation_prefers_the_change_that_touches_the_culprit(fx):
    conn = fx.conn()
    opened = 2_000_000_000
    usd = record_deploy(
        conn, fx.shas["usd"], ts=opened - 600, synthetic=True
    )  # touches paymentservice
    cart = record_deploy(
        conn, fx.shas["cart0"], ts=opened - 120, synthetic=True
    )  # newer, unrelated
    assert usd is not None and cart is not None and "paymentservice" in usd.predicted_components
    ranked = candidates(conn, opened, "paymentservice", "lookup paymentservice: no such host")
    assert ranked[0].commit == fx.shas["usd"]
    assert ranked[0].evidence["touches_culprit"] is True
    assert ranked[1].commit == fx.shas["cart0"] and ranked[1].evidence["touches_culprit"] is False
    assert record_deploy(conn, "not-a-commit") is None


def test_alert_posts_the_culprit(fx):
    conn = fx.conn()
    engine = run_engine(conn, stream(fault=True))
    full = get_incident(conn, engine.closed[0].id)
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert send_alert("https://example.invalid/webhook", full, client=client)
    assert "paymentservice" in sent[0]["content"]
    assert send_alert("", full) is False


def test_fault_scenarios_record_ground_truth_and_synthetic_deploys(fx, tmp_path):
    ran: list[list[str]] = []
    truth = inject("payment-down", ran.append, tmp_path, conn=fx.conn(), deploys=True)
    assert ran == [["scale", "deployment/paymentservice", "--replicas=0"]]
    assert truth["culprit"] == "paymentservice" and truth["synthetic"] is True
    guilty = truth["deploys"]["guilty"]
    files = [
        r[0]
        for r in fx.conn().execute(
            "SELECT ch.lineage FROM changes ch JOIN commits c ON c.id = ch.commit_id WHERE c.sha = ?",
            (guilty,),
        )
    ]
    assert any(f.startswith("src/paymentservice/") for f in files)
    reverted = revert("payment-down", ran.append, tmp_path)
    assert ran[-1] == ["scale", "deployment/paymentservice", "--replicas=1"]
    assert reverted is not None and reverted["reverted_at"] >= truth["injected_at"]
    with pytest.raises(KeyError):
        inject("no-such-fault", ran.append, tmp_path)
    assert {"payment-down", "currency-crash", "checkout-bad-config"} <= set(SCENARIOS)


def test_incident_and_deploy_endpoints(fx):
    app_module.app.dependency_overrides[app_module.get_settings] = lambda: fx.settings
    c = TestClient(app_module.app)
    run_engine(fx.conn(), stream(fault=True))
    listed = c.get("/api/incidents").json()
    assert listed and listed[0]["culprit"] == "paymentservice"
    detail = c.get(f"/api/incidents/{listed[0]['id']}").json()
    assert detail["ranking"][0]["service"] == "paymentservice"
    assert c.get("/api/incidents/999999").status_code == 404
    assert c.post("/api/deploys", json={"commit": "nope"}).status_code == 404
    assert (
        c.post("/api/deploys", json={"commit": fx.shas["log"]}).json()["commit"] == fx.shas["log"]
    )


def test_error_detail_and_kubelet_noise():
    raw = (
        '2024-01-01T00:00:00Z {"message": "request error", "error": "dial tcp 10.0.0.1:50051: refused",'
        ' "severity": "error"}'
    )
    e = normalise(raw, "frontend")
    assert e is not None and e.message == "request error: dial tcp 10.0.0.1:50051: refused"
    assert is_stream_noise("failed to create fsnotify watcher: too many open files\n")
    assert not is_stream_noise(raw)


def test_a_silent_service_is_blamed_through_its_cluster_ip(fx):
    """FIXTURE shaped like the live run: paymentservice goes silent; only frontend logs errors, and
    they name paymentservice by its cluster IP. cartservice's IP is a prefix of it and must not match."""
    conn = fx.conn()
    set_meta(
        conn,
        "service_addresses",
        {"paymentservice": ["10.96.233.126"], "cartservice": ["10.96.233.12"]},
    )
    detail = (
        "failed to complete the order: rpc error: code = Internal desc = failed to charge card: could"
        " not charge the card: rpc error: code = Unavailable desc = connection error: desc ="
        ' "transport: Error while dialing: dial tcp 10.96.233.126:50051: connect: connection refused"'
    )
    lines: list[tuple[str, str]] = []
    for t in range(18 * 60):
        lines.append(
            ("frontend", _line(t, f"request complete path=/product/{t % 9} status=200", "info"))
        )
        if t < FAULT_AT and t % 5 == 0:
            lines.append(
                ("paymentservice", _line(t, f"Transaction processed: amount USD {t}", "info"))
            )
        if t >= FAULT_AT and t % 10 == 0:
            stamp = datetime.fromtimestamp(T0 + t + 0.2, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")
            body = {"message": "request error", "error": detail, "severity": "error"}
            lines.append(("frontend", f"{stamp} " + json.dumps(body)))
    events = sorted(
        (e for e in (normalise(line, s) for s, line in lines) if e is not None), key=lambda e: e.ts
    )
    engine = run_engine(conn, events)
    assert len(engine.closed) == 1
    roles = {r["service"]: r["role"] for r in engine.closed[0].data["ranking"]}
    assert roles.get("paymentservice") == "culprit" and roles["frontend"] == "victim"
    assert "cartservice" not in roles
