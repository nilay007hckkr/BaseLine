"""Neo4j and SQLite give the same answers, read by read, on the FIXTURE repository.

Integration test: needs `make graph-up`. It syncs the fixture graph into its own throwaway
namespace and removes it afterwards, so the main repository's graph is never touched."""

import uuid
from collections.abc import Iterator

import pytest

from hindsight.brain.ask import ask
from hindsight.brain.graph import neighbourhood
from hindsight.brain.impact import impact_report
from hindsight.core.config import load_settings
from hindsight.core.graphstore import GraphReader, Neo4jGraph, SqliteGraph, neo4j_driver
from hindsight.core.graphsync import drop_namespace, sync

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def stores(fx) -> Iterator[tuple[object, SqliteGraph, Neo4jGraph]]:
    driver = neo4j_driver(load_settings())
    if driver is None:
        pytest.skip("Neo4j is not running (make graph-up)")
    conn = fx.conn()
    ns = f"test-{uuid.uuid4().hex[:10]}"
    state = sync(conn, driver, ns=ns)
    assert state["nodes"] > 0 and state["edges"] > 0
    try:
        yield conn, SqliteGraph(conn), Neo4jGraph(driver, ns)
    finally:
        drop_namespace(driver, ns)


def _ids(conn, kind: str | None = None) -> list[int]:
    if kind:
        return [r[0] for r in conn.execute("SELECT id FROM nodes WHERE kind = ?", (kind,))]
    return [r[0] for r in conn.execute("SELECT id FROM nodes")]


def test_every_read_matches(stores):
    conn, sql, neo = stores
    pair: tuple[GraphReader, GraphReader] = (sql, neo)
    nodes = _ids(conn)
    a, b = (g.edges_around(nodes, 14) for g in pair)
    assert a == b
    everything = _ids(conn)
    for kind in ("BELONGS_TO", "MENTIONS", "MODIFIES", "AUTHORED_BY"):
        assert sql.incoming(everything, kind) == neo.incoming(everything, kind), kind
    files = _ids(conn, "File")
    assert sql.recent_commits(files, 40) == neo.recent_commits(files, 40)
    commits = _ids(conn, "Commit")
    hop = ("PART_OF", "FIXES", "REFERENCES"), ("ON",)
    assert sql.step(commits, *hop) == neo.step(commits, *hop)
    for kind in ("BELONGS_TO", "CALLS", "MENTIONS"):
        assert sql.pairs(kind) == neo.pairs(kind), kind
    keys = [r[0] for r in conn.execute("SELECT key FROM nodes")]
    assert sql.mentions(keys) == neo.mentions(keys)
    assert neo.mentions(keys), "the fixture has doc mentions, so this compares something"


def test_features_give_the_same_output_on_either_store(stores, fx):
    conn, sql, neo = stores
    for key in ("service:checkoutservice", "file:src/checkoutservice/main.go"):
        a = neighbourhood(conn, key, depth=2, graph=sql)
        b = neighbourhood(conn, key, depth=2, graph=neo)
        assert a is not None and b is not None
        assert a.model_dump(exclude={"backend"}) == b.model_dump(exclude={"backend"})
        assert (a.backend, b.backend) == ("sqlite", "neo4j")
    files = ["src/checkoutservice/main.go"]
    assert impact_report(conn, files=files, graph=sql) == impact_report(
        conn, files=files, graph=neo
    )
    question = "Why do we charge in USD?"
    assert ask(conn, question, repo=fx.repo, graph=sql) == ask(
        conn, question, repo=fx.repo, graph=neo
    )


def test_graph_reader_ignores_a_stale_or_foreign_sync(stores, fx):
    """The fixture DB was synced under a test namespace, not its own: reads must stay on SQLite."""
    from hindsight.core.graphstore import graph_reader

    conn, _, _ = stores
    assert graph_reader(conn, fx.settings).backend == "sqlite"
