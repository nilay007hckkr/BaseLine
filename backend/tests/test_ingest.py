import json
import sqlite3

import pytest

from hindsight.brain.gitlog import lineage_map, read_log, split_rename
from hindsight.core.db import add_edge, connect, upsert_node


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/a.go", (None, "src/a.go")),
        ("old.txt => new.txt", ("old.txt", "new.txt")),
        ("src/{frontend => web}/main.go", ("src/frontend/main.go", "src/web/main.go")),
        ("src/{ => web}/x.go", ("src/x.go", "src/web/x.go")),
    ],
)
def test_split_rename(raw, expected):
    assert split_rename(raw) == expected


def test_classification_and_rename_lineage(fx):
    commits = {c.sha: c for c in read_log(fx.repo)}
    assert commits[fx.shas["bot"]].is_bot and not commits[fx.shas["bot"]].ok
    assert commits[fx.shas["sweep"]].is_sweep and not commits[fx.shas["sweep"]].ok
    assert commits[fx.shas["usd"]].ok
    assert (
        lineage_map(list(commits.values()))["src/frontend/handlers.go"] == "src/frontend/handler.go"
    )


def test_stats_are_computed_from_data(fx):
    conn = fx.conn()
    stats = json.loads(conn.execute("SELECT value FROM meta WHERE key = 'stats'").fetchone()[0])
    assert stats["commits"] == len(fx.shas)
    assert stats["bot_commits"] == 1
    assert stats["sweep_commits"] == 1
    assert stats["bot_share"] == round(1 / len(fx.shas), 4)


def _edge(conn, src_key, dst_key, kind):
    return conn.execute(
        "SELECT e.provenance, e.evidence FROM edges e JOIN nodes a ON a.id = e.src JOIN nodes b ON b.id = e.dst"
        " WHERE a.key = ? AND b.key = ? AND e.kind = ?",
        (src_key, dst_key, kind),
    ).fetchone()


def test_linking_rules_and_provenance(fx):
    conn = fx.conn()
    # title suffix (#12) was a heuristic link, confirmed by the API's merge commit sha
    part_of = _edge(conn, f"commit:{fx.shas['usd']}", "gh:12", "PART_OF")
    assert part_of["provenance"] == "api"
    assert json.loads(part_of["evidence"])["via"] == "merge_commit_sha"
    assert (
        _edge(conn, f"commit:{fx.shas['retry']}", "gh:14", "PART_OF")["provenance"] == "heuristic"
    )
    assert _edge(conn, "gh:12", "gh:3", "FIXES")["provenance"] == "explicit"
    assert _edge(conn, f"commit:{fx.shas['usd']}", "gh:3", "FIXES")["provenance"] == "explicit"
    assert conn.execute("SELECT kind FROM nodes WHERE key = 'gh:12'").fetchone()[0] == "PR"
    assert (
        _edge(conn, "review:7001", "file:src/checkoutservice/main.go", "ABOUT")["provenance"]
        == "api"
    )
    assert (
        _edge(conn, "service:checkoutservice", "service:paymentservice", "CALLS")["provenance"]
        == "static"
    )
    assert _edge(conn, "service:frontend", "service:cartservice", "CALLS") is not None
    readme = "chunk:README.md#0"  # "Shop > Checkout"
    assert _edge(conn, readme, "service:checkoutservice", "MENTIONS")["provenance"] == "heuristic"


def test_every_edge_has_evidence_and_known_provenance(fx):
    conn = fx.conn()
    bad = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE evidence IN ('', '{}') OR provenance NOT IN"
        " ('explicit','api','static','statistical','runtime','heuristic')"
    ).fetchone()[0]
    assert bad == 0


def test_injection_sample_is_flagged(fx):
    conn = fx.conn()
    row = conn.execute(
        "SELECT s.flagged FROM spans s JOIN nodes n ON n.id = s.node_id"
        " WHERE n.key = 'comment:9002'"
    ).fetchone()
    assert row["flagged"] == 1


def test_edges_without_evidence_or_with_unknown_provenance_are_rejected(tmp_path):
    conn = connect(tmp_path / "t.db")
    a = upsert_node(conn, "File", "file:a")
    b = upsert_node(conn, "File", "file:b")
    with pytest.raises(ValueError, match="no evidence"):
        add_edge(conn, a, b, "X", "explicit", {})
    with pytest.raises(ValueError, match="unknown provenance"):
        add_edge(conn, a, b, "X", "guess", {"k": 1})
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO edges(src, dst, kind, provenance, evidence) VALUES (?, ?, 'X', 'explicit', '{}')",
            (a, b),
        )


def test_heuristic_link_never_overwrites_a_stronger_one(tmp_path):
    conn = connect(tmp_path / "t.db")
    a = upsert_node(conn, "Commit", "commit:a")
    b = upsert_node(conn, "PR", "gh:1")
    add_edge(conn, a, b, "PART_OF", "api", {"via": "merge_commit_sha"})
    add_edge(conn, a, b, "PART_OF", "heuristic", {"via": "title_suffix"})
    assert conn.execute("SELECT provenance FROM edges").fetchone()[0] == "api"
