import dataclasses

from fastapi.testclient import TestClient

from hindsight.api import app as app_module


def client(fx) -> TestClient:
    app_module.app.dependency_overrides[app_module.get_settings] = lambda: fx.settings
    return TestClient(app_module.app)


def test_every_endpoint_answers(fx):
    c = client(fx)
    assert c.get("/api/health").json() == {"status": "ok"}
    assert c.get("/api/repo/stats").json()["stats"]["commits"] == len(fx.shas)
    assert (
        c.post("/api/ask", json={"question": "Why do we charge in USD?"}).json()["status"]
        == "answered"
    )
    report = c.post("/api/impact", json={"files": ["src/checkoutservice/main.go"]}).json()
    assert report["changed_files"] == ["src/checkoutservice/main.go"]
    assert c.get("/api/commits", params={"limit": 5}).status_code == 200
    graph = c.get("/api/graph", params={"key": "service:checkoutservice"}).json()
    assert graph["nodes"] and all(e["provenance"] for e in graph["edges"])
    assert c.get("/api/search", params={"q": "checkout"}).json()
    assert c.get("/api/risk").status_code == 200
    assert c.get("/api/scorecard").status_code == 200
    assert "/api/impact" in c.get("/openapi.json").json()["paths"]


def test_bad_requests_are_rejected(fx):
    c = client(fx)
    assert c.post("/api/impact", json={}).status_code == 422
    assert c.post("/api/ask", json={"question": "x"}).status_code == 422
    assert c.get("/api/graph", params={"key": "file:nope"}).status_code == 404


def test_map_starts_at_the_most_linked_service_of_any_repository(fx, tmp_path):
    from hindsight.brain.graph import default_centre
    from hindsight.core.db import connect

    conn = fx.conn()
    start = default_centre(conn)
    links = {
        key: n
        for key, n in conn.execute(
            "SELECT n.key, (SELECT COUNT(*) FROM edges WHERE src = n.id OR dst = n.id)"
            " FROM nodes n WHERE n.kind = 'Service'"
        )
    }
    assert start == max(sorted(links), key=lambda k: links[k])  # most linked; ties by key
    graph = client(fx).get("/api/graph").json()  # no key, as the Map sends on first load
    assert graph["center"] == start and graph["nodes"]

    empty = dataclasses.replace(fx.settings, db_path=tmp_path / "empty.db")
    connect(empty.db_path).close()
    app_module.app.dependency_overrides[app_module.get_settings] = lambda: empty
    try:
        r = TestClient(app_module.app).get("/api/graph")
    finally:
        app_module.app.dependency_overrides.clear()
    assert r.status_code == 404 and "ingest a repository first" in r.json()["detail"]


def test_document_upload_endpoints(fx):
    c = client(fx)
    doc = b"# Cart runbook (FIXTURE)\n\n## Cart empty\n\nIf cartservice loses carts, check its storage.\n"
    report = c.post(
        "/api/docs",
        files={"file": ("cart-runbook.md", doc, "text/markdown")},
        data={"doc_type": "runbook"},
    ).json()
    assert report["status"] == "created" and report["runbook_links"] >= 1
    listed = {d["key"]: d for d in c.get("/api/docs").json()}
    assert listed[report["doc_key"]]["source"] == "upload"
    assert c.get("/api/services/cartservice/runbooks").json()
    blob = listed[report["doc_key"]]["url"]
    assert c.get(blob).content == doc
    bad = c.post("/api/docs", files={"file": ("tool.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 415
    assert c.get("/api/docs/blob/../../etc/passwd").status_code == 404


def test_scorecard_reads_both_result_files(tmp_path):
    """FIXTURE result files: the numbers here are made up to test the reader, not results."""
    import json

    from hindsight.api.service import scorecard

    def m(v: float) -> dict[str, float]:
        return {"value": v, "ci_low": v - 0.1, "ci_high": v + 0.1}

    (tmp_path / "impact-20260101T000000Z.json").write_text(
        json.dumps(
            {
                "n_queries": 7,
                "command": "make eval",
                "methods": {"hindsight": {"P@5": m(0.5)}, "same_dir": {"P@5": m(0.3)}},
            }
        )
    )
    (tmp_path / "rca-20260101T000000Z.json").write_text(
        json.dumps(
            {
                "n_runs": 4,
                "detected": 3,
                "command": "make eval-rca RUNS=4",
                "ttd_s": {"median": 30, "p90": 40},
                "false_alerts_per_hour": 0.0,
                "culprit": {
                    "hindsight": {"top1": m(0.75), "top3": m(1.0)},
                    "baseline_most_errors": {"top1": m(0.25), "top3": m(0.5)},
                },
                "correlation": {
                    "hindsight": {"hit1": m(0.66), "hit3": m(1.0)},
                    "baseline_most_recent_deploy": {"hit1": m(0.33), "hit3": m(1.0)},
                },
            }
        )
    )
    cards = scorecard(tmp_path)
    assert [c.suite for c in cards] == ["impact", "rca", "rca", "rca", "rca"]
    assert [c.metric for c in cards][1:] == [
        "Culprit top-1",
        "Culprit top-3",
        "Change Hit@1",
        "Change Hit@3",
    ]
    culprit = cards[1]
    assert culprit.synthetic and culprit.n == 4 and culprit.baseline_value == 0.25
    assert cards[3].n == 3 and "3 of 4" in cards[3].note
    assert scorecard(tmp_path / "missing") == []


def test_visitors_can_rate_answers_and_see_the_tally(fx, tmp_path):
    import shutil

    from hindsight.core.config import load_settings

    db = tmp_path / "copy.db"
    shutil.copy(fx.settings.db_path, db)
    settings = load_settings({"DATA_DIR": str(fx.settings.data_dir), "DB_PATH": str(db)})
    app_module.app.dependency_overrides[app_module.get_settings] = lambda: settings
    c = TestClient(app_module.app)
    assert c.get("/api/answer-checks").json()["n"] == 0
    good = {
        "question": "Why USD?",
        "status": "answered",
        "mode": "extractive",
        "verdict": "supported",
    }
    c.post("/api/answer-checks", json=good)
    c.post("/api/answer-checks", json={**good, "verdict": "unsupported"})
    tally = c.post(
        "/api/answer-checks",
        json={**good, "status": "not_found", "mode": "none", "verdict": "right_not_found"},
    ).json()
    assert (tally["n"], tally["answered_rated"], tally["supported"]) == (3, 2, 1)
    assert (tally["not_found_rated"], tally["right_not_found"]) == (1, 1)
    # a verdict that does not fit the answer is rejected
    bad = c.post("/api/answer-checks", json={**good, "verdict": "right_not_found"})
    assert bad.status_code == 422
