"""Incident briefs: core evidence from code, a bounded read-only tool loop, and claim checks.
Runs on the FIXTURE payment outage from test_logsense with scripted model replies."""

import json

import pytest
from fastapi.testclient import TestClient
from test_logsense import run_engine, stream

from hindsight.api import app as app_module
from hindsight.brain.ask import validate_claims
from hindsight.brain.uploads import ingest_upload
from hindsight.core.llm import Chain
from hindsight.correlate.deploys import record_deploy
from hindsight.correlate.investigate import MAX_STEPS, _blames_another, investigate
from hindsight.logsense.incidents import get_incident

SERVICES = {"paymentservice", "checkoutservice", "frontend", "cartservice"}


class Script:
    """Stands in for a model: replies in order, repeating the last reply; records every prompt."""

    name = "fake"  # not "ollama", so the chain counts as external
    model = "fake-1"

    def __init__(self, *replies: dict) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.replies[min(len(self.prompts), len(self.replies)) - 1])


def _incident(fx):
    conn = fx.conn()
    engine = run_engine(conn, stream(fault=True))
    opened = int(engine.closed[0].opened_at)
    record_deploy(conn, fx.shas["usd"], ts=opened - 300, synthetic=True)  # touches paymentservice
    incident = get_incident(conn, engine.closed[0].id)
    assert incident is not None
    return conn, incident


def _chain(conn, provider):
    return Chain(conn, [provider], "off")


def _id(brief, node_key_suffix):
    return next(e.id for e in brief.evidence if e.node_key.endswith(node_key_suffix))


def test_without_a_model_the_brief_quotes_code_written_evidence(fx):
    conn, incident = _incident(fx)
    brief = investigate(conn, incident)
    assert brief.status == "answered" and brief.mode == "extractive"
    assert brief.culprit == "paymentservice"
    assert brief.steps[0].by == "code" and brief.steps[0].evidence_ids
    assert "Code ranked paymentservice as culprit" in brief.claims[0].text
    ids = {e.id for e in brief.evidence}
    assert all(set(c.cites) <= ids for c in brief.claims)
    assert any(e.node_key == f"commit:{fx.shas['usd']}" for e in brief.evidence)
    assert any("no LLM provider configured" in n for n in brief.notes)


def test_the_model_reads_a_pattern_then_finishes_with_cited_claims(fx):
    conn, incident = _incident(fx)
    tid = incident.templates[0].id
    rank = f"incident:{incident.id}:rank:paymentservice"
    model = Script(
        {"call": {"tool": "pattern_samples", "args": {"template_id": tid}}},
        {
            "finish": {
                "claims": [{"text": "Code ranked paymentservice as culprit.", "cites": ["E1"]}]
            }
        },
    )
    brief = investigate(conn, incident, chain=_chain(conn, model))
    assert brief.mode == "llm" and brief.claims[0].cites == ["E1"]
    assert brief.evidence[0].node_key == rank
    step = brief.steps[1]
    assert step.by == "model" and step.tool == "pattern_samples" and step.status == "ok"
    assert step.evidence_ids and all(i in {e.id for e in brief.evidence} for i in step.evidence_ids)
    assert "Steps so far" in model.prompts[1] and step.evidence_ids[0] in model.prompts[1]


def test_a_claim_that_blames_a_victim_is_removed(fx):
    conn, incident = _incident(fx)
    model = Script(
        {
            "finish": {
                "claims": [
                    {
                        "text": "The frontend is the culprit; its errors name paymentservice.",
                        "cites": ["E1"],
                    },
                    {"text": "Code ranked paymentservice as culprit.", "cites": ["E1"]},
                ]
            }
        }
    )
    brief = investigate(conn, incident, chain=_chain(conn, model))
    assert [c.text for c in brief.claims] == ["Code ranked paymentservice as culprit."]
    assert any("root cause other than paymentservice" in n for n in brief.notes)


@pytest.mark.parametrize(
    ("claim", "blames_another"),
    [
        ("paymentservice is the root cause.", False),
        ("The payment service is the likely culprit.", False),
        ("Frontend errors were caused by paymentservice.", False),
        ("The frontend logged the most errors.", False),
        ("frontend is the culprit.", True),
        ("The culprit is checkoutservice.", True),
        ("Checkout failures caused the order errors.", True),
        ("frontend, not paymentservice, is the root cause.", True),
    ],
)
def test_root_cause_wording_must_name_the_ranked_culprit(claim, blames_another):
    assert _blames_another(claim, "paymentservice", SERVICES) is blames_another


def test_calls_outside_the_incident_are_refused_and_the_budget_holds(fx):
    conn, incident = _incident(fx)
    model = Script(
        {"call": {"tool": "shell", "args": {"cmd": "kubectl delete ns default"}}},
        {"call": {"tool": "impact", "args": {"commit": "deadbeefcafe"}}},
        {"call": {"tool": "past_incidents", "args": {"service": "not-a-service"}}},
    )
    brief = investigate(conn, incident, chain=_chain(conn, model))
    model_steps = [s for s in brief.steps if s.by == "model"]
    assert len(model_steps) == MAX_STEPS
    assert all(s.status == "refused" and not s.evidence_ids for s in model_steps)
    assert model_steps[0].note.startswith("unknown tool")
    assert len(model.prompts) == MAX_STEPS + 1  # the last turn offers no tools
    assert "no tool calls left" in model.prompts[-1]
    assert any(f"did not finish within {MAX_STEPS}" in n for n in brief.notes)
    assert brief.mode == "extractive"  # falls back instead of showing nothing


def test_candidate_impact_and_past_incidents_are_allowed(fx):
    conn, incident = _incident(fx)
    model = Script(
        {"call": {"tool": "impact", "args": {"commit": fx.shas["usd"][:7]}}},
        {"call": {"tool": "past_incidents", "args": {"service": "paymentservice"}}},
        {"finish": {"claims": []}},
    )
    brief = investigate(conn, incident, chain=_chain(conn, model))
    impact, past = brief.steps[1], brief.steps[2]
    assert impact.status == "ok" and impact.evidence_ids
    assert past.status in ("ok", "empty") and past.evidence_ids
    assert brief.mode == "extractive"  # no claims proposed: quoted evidence instead


def test_follow_up_questions_say_not_found_when_unanswered(fx):
    conn, incident = _incident(fx)
    model = Script({"finish": {"claims": []}})
    brief = investigate(
        conn, incident, question="Was the database slow?", chain=_chain(conn, model)
    )
    assert brief.status == "not_found" and brief.claims == []
    assert "Was the database slow?" in model.prompts[0]
    offline = investigate(conn, incident, question="Was the database slow?")
    assert offline.status == "not_found"
    assert any("need a configured model" in n for n in offline.notes)


def test_without_a_ranked_culprit_there_is_nothing_to_explain(fx):
    conn, incident = _incident(fx)
    brief = investigate(conn, incident.model_copy(update={"ranking": []}))
    assert brief.status == "not_found" and brief.mode == "none" and brief.evidence == []


def test_uploaded_runbooks_stay_local_with_an_external_model(fx, tmp_path):
    conn, incident = _incident(fx)
    with conn:
        ingest_upload(
            conn,
            tmp_path,
            "payment-outage-runbook.md",
            b"# Payment outage runbook (FIXTURE)\n\n## paymentservice down\n\n"
            b"Page the quokka on-call rota, then scale paymentservice back to one replica.\n",
            "runbook",
        )
    incident = get_incident(conn, incident.id)
    model = Script({"finish": {"claims": []}})
    brief = investigate(conn, incident, chain=_chain(conn, model))
    assert any("quokka" in e.snippet for e in brief.evidence)  # shown to the engineer
    assert all("quokka" not in p for p in model.prompts)  # never sent to the external model
    allowed = Script({"finish": {"claims": []}})
    investigate(conn, incident, chain=_chain(conn, allowed), uploads_to_external=True)
    assert any("quokka" in p for p in allowed.prompts)


def test_malformed_model_claims_are_ignored():
    texts = {"E1": "paymentservice is down"}
    raw = [
        "not a claim",
        {"text": "paymentservice is down", "cites": "E1"},
        {"text": "paymentservice is down", "cites": [{"id": "E1"}]},
        {"text": "paymentservice is down", "cites": ["E1"]},
    ]
    assert [c.cites for c in validate_claims(raw, texts, {"E1"})] == [["E1"]]
    assert validate_claims({"claims": []}, texts, {"E1"}) == []


def test_investigate_endpoint(fx):
    app_module.app.dependency_overrides[app_module.get_settings] = lambda: fx.settings
    c = TestClient(app_module.app)
    _, incident = _incident(fx)
    resp = c.post(f"/api/incidents/{incident.id}/investigate", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["culprit"] == "paymentservice" and body["mode"] == "extractive"
    assert c.post("/api/incidents/999999/investigate", json={}).status_code == 404
    assert (
        c.post(f"/api/incidents/{incident.id}/investigate", json={"question": "?"}).status_code
        == 422
    )
