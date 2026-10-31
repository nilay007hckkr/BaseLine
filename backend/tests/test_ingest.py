import json
import os
import sqlite3
import subprocess

import pytest

from hindsight.brain import ingest as ingest_module
from hindsight.brain.gitlog import lineage_map, read_log, split_rename
from hindsight.core.config import load_settings
from hindsight.core.db import add_edge, connect, upsert_node


def test_reingest_follows_new_layout_rules(tmp_path, monkeypatch):
    """FIXTURE: a tiny go-kratos-style repository (app/<service>/…) written by the test. The first
    ingest runs with the old rule (no services outside src/), the second with the current one: no
    file may keep its old folder link, and the emptied folder node disappears."""
    settings = load_settings(
        {"DATA_DIR": str(tmp_path / "data"), "TARGET_REPO_URL": "https://github.com/acme/beer"}
    )
    repo = settings.repos_dir / "acme__beer"
    files = {
        "api/user/v1/user.proto": "service User {\n  rpc Get (A) returns (B);\n}\n",
        "app/user/service/cmd/server/main.go": "package main\n",
        "app/shop/interface/cmd/server/main.go": "package main\n",
        "app/shop/interface/internal/data/data.go": "package data\n// userv1.NewUserClient(conn)\n",
    }
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Ana",
        "GIT_AUTHOR_EMAIL": "ana@example.com",
        "GIT_COMMITTER_NAME": "Ana",
        "GIT_COMMITTER_EMAIL": "ana@example.com",
    }
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)

    quiet = lambda *_: None  # noqa: E731
    monkeypatch.setattr(ingest_module, "discover_services", lambda head: set())
    ingest_module.ingest(settings, fetch_forge=False, log=quiet)
    with connect(settings.db_path) as before:  # the old rule really grouped app/ as one folder
        assert before.execute("SELECT 1 FROM nodes WHERE key = 'component:app'").fetchone()
    monkeypatch.undo()
    ingest_module.ingest(settings, fetch_forge=False, log=quiet)

    conn = connect(settings.db_path)
    owners = {
        path: (kind, key, json.loads(evidence)["pattern"])
        for path, kind, key, evidence in conn.execute(
            "SELECT f.title, c.kind, c.key, e.evidence FROM edges e JOIN nodes f ON f.id = e.src"
            " JOIN nodes c ON c.id = e.dst WHERE e.kind = 'BELONGS_TO' AND f.kind = 'File'"
        )
    }
    assert owners["app/shop/interface/internal/data/data.go"] == (
        "Service",
        "service:shop",
        "app/shop/",
    )
    assert owners["api/user/v1/user.proto"] == ("Component", "component:api", "api/")
    assert len(owners) == len(files)  # one link per file: the old app/ links are gone
    assert not conn.execute("SELECT 1 FROM nodes WHERE key = 'component:app'").fetchone()
    calls = conn.execute(
        "SELECT a.title, b.title FROM edges e JOIN nodes a ON a.id = e.src JOIN nodes b ON b.id = e.dst"
        " WHERE e.kind = 'CALLS'"
    ).fetchall()
    assert [tuple(r) for r in calls] == [("shop", "user")]


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
