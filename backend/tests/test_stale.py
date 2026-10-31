"""Docs that may be out of date. A small hand-built graph (FIXTURE data only)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from hindsight.api import app as app_module
from hindsight.brain.impact import impact_report
from hindsight.brain.stale import doc_name, stale_docs
from hindsight.core.db import add_edge, connect, upsert_node

DAY = 86_400


def _commit(conn: sqlite3.Connection, sha: str, ts: int, files: list[int], ok: bool = True) -> None:
    cid = upsert_node(conn, "Commit", f"commit:{sha}", title=f"change {sha}", ts=ts)
    conn.execute(
        "INSERT INTO commits(id, sha, ts, author_key, n_files, is_bot, is_sweep, is_merge, ok)"
        " VALUES (?, ?, ?, 'ana', ?, ?, 0, 0, ?)",
        (cid, sha, ts, len(files), int(not ok), int(ok)),
    )
    for f in files:
        add_edge(conn, cid, f, "MODIFIES", "explicit", {"sha": sha})


def _graph(path: Path, doc_ts: int) -> sqlite3.Connection:
    conn = connect(path)
    with conn:
        f = upsert_node(conn, "File", "file:src/cart/main.go", title="src/cart/main.go")
        d = upsert_node(conn, "Doc", "doc:docs/cart.md", title="docs/cart.md", url="u", ts=doc_ts)
        ch = upsert_node(conn, "DocChunk", "chunk:docs/cart.md#0", title="docs/cart.md § Cart")
        add_edge(conn, ch, d, "PART_OF", "explicit", {"section": "Cart"})
        add_edge(conn, ch, f, "MENTIONS", "heuristic", {"surface": "src/cart/main.go"})
        for i in range(3):
            _commit(conn, f"a{i}", 1000 * DAY + i * DAY, [f])
        _commit(conn, "bot1", 1010 * DAY, [f], ok=False)  # bots do not count
    return conn


def test_a_doc_older_than_three_changes_to_what_it_describes_is_flagged(tmp_path):
    conn = _graph(tmp_path / "db.sqlite", doc_ts=999 * DAY)
    [item] = stale_docs(conn)
    assert item.target == "docs/cart.md" and item.target_kind == "doc"
    assert (
        "since then 3 commits changed what it describes (src/cart/main.go)" in item.reasons[0].text
    )
    assert item.reasons[0].provenance == "statistical"
    assert [r.text.split(" ")[1] for r in item.reasons[1:]] == ["a2", "a1", "a0"]  # newest first


def test_a_doc_edited_after_the_changes_is_not_flagged(tmp_path):
    conn = _graph(tmp_path / "db.sqlite", doc_ts=1001 * DAY)  # only one change since
    assert stale_docs(conn) == []


def test_impact_says_a_mentioned_doc_may_be_out_of_date(tmp_path):
    conn = _graph(tmp_path / "db.sqlite", doc_ts=999 * DAY)
    report = impact_report(conn, files=["src/cart/main.go"])
    [doc] = report.docs
    assert doc.reasons[0].text.startswith("may be out of date: last edited")


def test_doc_names_and_the_api(fx):
    assert doc_name("runbook.md@v2 § Steps") == "runbook.md"
    app_module.app.dependency_overrides[app_module.get_settings] = lambda: fx.settings
    try:
        r = TestClient(app_module.app).get("/api/docs/stale")
        assert r.status_code == 200 and isinstance(r.json(), list)
    finally:
        app_module.app.dependency_overrides.clear()
