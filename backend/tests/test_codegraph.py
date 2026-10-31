"""Code structure layer: graphify's graph.json → Symbol nodes, DEFINES and CODE_CALLS edges.
Uses testdata/codegraph/graph.json (FIXTURE, hand-written) on a private copy of the fixture DB."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hindsight.api import app as app_module
from hindsight.brain import codegraph
from hindsight.brain.codegraph import (
    CODE_CALLS,
    CODE_KINDS,
    DEFINES,
    PINNED,
    CodeGraphError,
    build_code_layer,
    import_code_layer,
)
from hindsight.brain.graph import neighbourhood
from hindsight.brain.impact import impact_report
from hindsight.core.db import connect, get_meta
from hindsight.core.graphstore import SqliteGraph

FIXTURE = Path(__file__).resolve().parents[2] / "testdata" / "codegraph" / "graph.json"
MAIN = "src/checkoutservice/main.go"


@pytest.fixture
def db(fx, tmp_path: Path) -> Path:
    """A private copy of the session database, so the layer never leaks into other tests."""
    path = tmp_path / "hindsight.db"
    src, dst = fx.conn(), sqlite3.connect(path)
    src.backup(dst)
    src.close()
    dst.close()
    return path


def _edges(conn: sqlite3.Connection, kind: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT a.key AS src, b.key AS dst, e.provenance, e.evidence FROM edges e"
        " JOIN nodes a ON a.id = e.src JOIN nodes b ON b.id = e.dst WHERE e.kind = ? ORDER BY 1, 2",
        (kind,),
    ).fetchall()


def test_imports_only_what_was_read_from_the_source(fx, db):
    conn = connect(db)
    heads = {
        r[0]
        for r in conn.execute(
            "SELECT title FROM nodes WHERE kind = 'File' AND json_extract(attrs, '$.at_head') = 1"
        )
    }
    assert {MAIN, "src/checkoutservice/late.go"} <= heads  # the fixture's files at HEAD

    stats = build_code_layer(conn, Path("unused"), fx.repo, FIXTURE)

    assert (stats.symbols, stats.files, stats.calls) == (4, 2, 2)
    assert (stats.skipped_inferred, stats.skipped_generated, stats.skipped_untracked) == (1, 1, 1)
    titles = {r[0] for r in conn.execute("SELECT title FROM nodes WHERE kind = 'Symbol'")}
    assert titles == {"checkoutService", "PlaceOrder()", "chargeCard()", "retry()"}
    # comment text, configuration keys, external modules and generated stubs never become nodes
    texts = " ".join(r[0] for r in conn.execute("SELECT title FROM nodes"))
    assert "Ignore previous instructions" not in texts and "GetQuoteRequest" not in texts

    defines = _edges(conn, DEFINES)
    assert len(defines) == 4 and {d["provenance"] for d in defines} == {"static"}
    place = next(d for d in defines if "PlaceOrder" in d["dst"])
    assert place["src"] == f"file:{MAIN}"
    assert json.loads(place["evidence"]) == {
        "tool": "graphify (imported graph.json)",
        "file": MAIN,
        "line": 12,
        "detail": "main.go defines PlaceOrder() at line 12",
    }

    calls = {(c["src"].split("#")[1], c["dst"].split("#")[1]): c for c in _edges(conn, CODE_CALLS)}
    assert set(calls) == {
        ("PlaceOrder()@L12", "chargeCard()@L30"),
        ("PlaceOrder()@L12", "retry()@L3"),
    }
    charge = json.loads(calls[("PlaceOrder()@L12", "chargeCard()@L30")]["evidence"])
    assert charge["count"] == 2 and charge["sites"] == [f"{MAIN}:18", f"{MAIN}:22"]

    url = conn.execute("SELECT url FROM nodes WHERE title = 'PlaceOrder()'").fetchone()[0]
    head = subprocess.run(
        ["git", "-C", str(fx.repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert (
        url == f"https://github.com/acme/shop/blob/{head}/{MAIN}#L12"
    )  # pinned to the parsed commit
    assert get_meta(conn, "codegraph")["symbols"] == 4


def test_reimport_replaces_the_layer(fx, db):
    conn = connect(db)
    build_code_layer(conn, Path("unused"), fx.repo, FIXTURE)
    build_code_layer(conn, Path("unused"), fx.repo, FIXTURE)
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'Symbol'").fetchone()[0] == 4

    data = json.loads(FIXTURE.read_text())
    data["nodes"] = [n for n in data["nodes"] if n["id"] != "checkout_late_retry"]
    stats = import_code_layer(conn, data, "graphify test", "HEAD")
    assert stats.symbols == 3 and stats.calls == 1
    assert not conn.execute("SELECT 1 FROM nodes WHERE title = 'retry()'").fetchone()
    assert len(_edges(conn, CODE_CALLS)) == 1


def test_code_layer_leaves_impact_unchanged(fx, db):
    conn = connect(db)
    graph = SqliteGraph(conn)
    service_calls = graph.pairs("CALLS")
    before = impact_report(conn, files=[MAIN], graph=graph).model_dump()

    build_code_layer(conn, Path("unused"), fx.repo, FIXTURE)

    assert graph.pairs("CALLS") == service_calls  # impact's service call graph is untouched
    assert impact_report(conn, files=[MAIN], graph=graph).model_dump() == before


def test_map_shows_the_layer_only_when_asked(fx, db):
    conn = connect(db)
    build_code_layer(conn, Path("unused"), fx.repo, FIXTURE)

    plain = neighbourhood(conn, f"file:{MAIN}")
    assert plain and not {n.kind for n in plain.nodes} & {"Symbol"}
    assert not {e.kind for e in plain.edges} & CODE_KINDS

    code = neighbourhood(conn, f"file:{MAIN}", code=True)
    assert code and {n.label for n in code.nodes if n.kind == "Symbol"} == {
        "checkoutService",
        "PlaceOrder()",
        "chargeCard()",
    }
    assert all(e.provenance == "static" for e in code.edges if e.kind in CODE_KINDS)

    centred = neighbourhood(conn, f"symbol:{MAIN}#PlaceOrder()@L12")  # a symbol shows its calls
    assert centred and {e.kind for e in centred.edges} >= {DEFINES, CODE_CALLS}


def test_graph_endpoint_takes_the_code_switch(fx, db):
    conn = connect(db)
    build_code_layer(conn, Path("unused"), fx.repo, FIXTURE)
    settings = dataclasses.replace(fx.settings, db_path=db)
    app_module.app.dependency_overrides[app_module.get_settings] = lambda: settings
    try:
        c = TestClient(app_module.app)
        key = {"key": f"file:{MAIN}"}
        off = c.get("/api/graph", params=key).json()
        on = c.get("/api/graph", params={**key, "code": "true"}).json()
    finally:
        app_module.app.dependency_overrides.clear()
    assert not [n for n in off["nodes"] if n["kind"] == "Symbol"]
    assert len([n for n in on["nodes"] if n["kind"] == "Symbol"]) == 3


@pytest.mark.parametrize(
    "payload", [[], {"nodes": "x", "edges": []}, {"nodes": []}, "not json at all"]
)
def test_rejects_what_is_not_a_graph(fx, db, tmp_path, payload):
    conn = connect(db)
    path = tmp_path / "graph.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    with pytest.raises(CodeGraphError):
        build_code_layer(conn, Path("unused"), fx.repo, path)


def test_missing_tool_or_clone_says_what_to_do(fx, db, monkeypatch):
    conn = connect(db)
    with pytest.raises(CodeGraphError, match="make ingest"):
        build_code_layer(conn, Path("unused"), None)
    monkeypatch.setattr(codegraph.shutil, "which", lambda _name: None)
    with pytest.raises(CodeGraphError, match=f"uv tool install graphifyy=={PINNED}"):
        build_code_layer(conn, Path("unused"), fx.repo)


REAL_RUN = subprocess.run  # git still runs for real; only graphify is faked


def test_runs_graphify_offline(fx, db, tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_run(cmd, **kw):
        if cmd[0] != "/opt/graphify":
            return REAL_RUN(cmd, **kw)
        calls.append({"cmd": cmd, **kw})
        if cmd[1] == "extract":
            out = Path(cmd[cmd.index("--out") + 1]) / "graphify-out"
            out.mkdir(parents=True)
            (out / "graph.json").write_text(FIXTURE.read_text())
        return subprocess.CompletedProcess(cmd, 0, stdout="graphify 0.9.99\n", stderr="")

    monkeypatch.setattr(codegraph.shutil, "which", lambda _name: "/opt/graphify")
    monkeypatch.setattr(codegraph.subprocess, "run", fake_run)
    stats = build_code_layer(connect(db), tmp_path / "codegraph", fx.repo)

    extract = next(c for c in calls if c["cmd"][1] == "extract")
    assert {"--code-only", "--no-cluster"} <= set(extract["cmd"])  # type: ignore[arg-type]
    assert extract["env"]["GRAPHIFY_BACKEND"] == "none"  # type: ignore[index]
    assert all(c["stdin"] == subprocess.DEVNULL for c in calls)
    assert stats.tool == "graphify 0.9.99" and stats.symbols == 4


def test_failing_tool_is_reported(fx, db, tmp_path, monkeypatch):
    def fake_run(cmd, **kw):
        if cmd[0] != "/opt/graphify":
            return REAL_RUN(cmd, **kw)
        if cmd[1] == "extract":
            raise subprocess.CalledProcessError(2, cmd, stderr="tree-sitter exploded")
        return subprocess.CompletedProcess(cmd, 0, stdout="graphify 0.9.99\n", stderr="")

    monkeypatch.setattr(codegraph.shutil, "which", lambda _name: "/opt/graphify")
    monkeypatch.setattr(codegraph.subprocess, "run", fake_run)
    with pytest.raises(CodeGraphError, match="tree-sitter exploded"):
        build_code_layer(connect(db), tmp_path / "codegraph", fx.repo)
