"""Copy the SQLite graph into Neo4j (TRD §0.2): idempotent, batched, one namespace per repository.

Every node becomes (:Node:<Kind> {ns, uid, sid, key, title, url, ts, ...}); every edge becomes a
relationship of type <KIND> with provenance, the evidence JSON (as text) and weight. Anything in
the namespace that was not written by this run is removed, so Neo4j ends up holding exactly what
SQLite holds. The counts are saved under meta 'graph_sync'; graph_reader() compares them before it
trusts Neo4j."""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from collections import defaultdict
from typing import Any

from hindsight.core.db import set_meta
from hindsight.core.graphstore import graph_counts, namespace

BATCH = 2000
_LABEL = re.compile(r"^[A-Z][A-Za-z]*$")
_TYPE = re.compile(r"^[A-Z][A-Z_]*$")

SCHEMA = [
    "CREATE CONSTRAINT hs_node_uid IF NOT EXISTS FOR (n:Node) REQUIRE n.uid IS UNIQUE",
    "CREATE INDEX hs_node_ns_sid IF NOT EXISTS FOR (n:Node) ON (n.ns, n.sid)",
    "CREATE INDEX hs_node_ns_key IF NOT EXISTS FOR (n:Node) ON (n.ns, n.key)",
]


def _batches(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [rows[i : i + BATCH] for i in range(0, len(rows), BATCH)]


def _node_rows(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in conn.execute(
        "SELECT n.id, n.kind, n.key, n.title, n.url, COALESCE(c.ts, n.ts) AS ts, n.attrs, c.is_bot"
        " FROM nodes n LEFT JOIN commits c ON c.id = n.id"
    ):
        if not _LABEL.match(r["kind"]):
            raise ValueError(f"node kind {r['kind']!r} cannot be a Neo4j label")
        attrs = json.loads(r["attrs"])
        by_kind[r["kind"]].append(
            {
                "sid": r["id"],
                "key": r["key"],
                "kind": r["kind"],
                "title": r["title"],
                "url": r["url"],
                "ts": r["ts"],
                "attrs": r["attrs"],
                "is_bot": None if r["is_bot"] is None else bool(r["is_bot"]),
                "superseded": bool(attrs.get("superseded", False)),
            }
        )
    return by_kind


def _edge_rows(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in conn.execute("SELECT id, src, dst, kind, weight, provenance, evidence, ts FROM edges"):
        if not _TYPE.match(r["kind"]):
            raise ValueError(f"edge kind {r['kind']!r} cannot be a Neo4j relationship type")
        by_kind[r["kind"]].append(
            {
                "sid": r["id"],
                "src": r["src"],
                "dst": r["dst"],
                "weight": r["weight"],
                "provenance": r["provenance"],
                "evidence": r["evidence"],
                "ts": r["ts"],
            }
        )
    return by_kind


def sync(
    conn: sqlite3.Connection, driver: Any, ns: str | None = None, database: str = "neo4j"
) -> dict[str, Any]:
    started = time.time()
    ns = ns or namespace(conn)
    run = uuid.uuid4().hex
    counts = graph_counts(conn)  # read first: anything added during the copy makes it stale

    def write(query: str, **params: Any) -> None:
        driver.execute_query(
            query, parameters_={"ns": ns, "run": run, **params}, database_=database
        )

    for statement in SCHEMA:
        driver.execute_query(statement, database_=database)
    nodes = _node_rows(conn)
    for kind, rows in sorted(nodes.items()):
        for chunk in _batches(rows):
            write(
                f"UNWIND $rows AS r MERGE (n:Node {{uid: $ns + '|' + r.key}}) SET n:{kind},"
                " n.ns = $ns, n.sid = r.sid, n.key = r.key, n.kind = r.kind, n.title = r.title,"
                " n.url = r.url, n.ts = r.ts, n.attrs = r.attrs, n.is_bot = r.is_bot,"
                " n.superseded = r.superseded, n.sync = $run",
                rows=chunk,
            )
    edges = _edge_rows(conn)
    for kind, rows in sorted(edges.items()):
        for chunk in _batches(rows):
            write(
                "UNWIND $rows AS r MATCH (a:Node {ns: $ns, sid: r.src}) MATCH (b:Node {ns: $ns, sid: r.dst})"
                f" MERGE (a)-[e:{kind} {{sid: r.sid}}]->(b) SET e.ns = $ns, e.weight = r.weight,"
                " e.provenance = r.provenance, e.evidence = r.evidence, e.ts = r.ts, e.sync = $run",
                rows=chunk,
            )
    # sweep what SQLite no longer has (edges first, then nodes with whatever still hangs on them)
    write("MATCH (:Node {ns: $ns})-[e]->() WHERE e.sync <> $run DELETE e")
    write("MATCH (n:Node {ns: $ns}) WHERE n.sync <> $run DETACH DELETE n")
    records, _, _ = driver.execute_query(
        "MATCH (n:Node {ns: $ns}) WITH count(n) AS nodes"
        " OPTIONAL MATCH (:Node {ns: $ns})-[e]->() RETURN nodes, count(e) AS edges",
        parameters_={"ns": ns},
        database_=database,
    )
    in_neo4j = {"nodes": records[0]["nodes"], "edges": records[0]["edges"]}
    if in_neo4j != {"nodes": counts["nodes"], "edges": counts["edges"]}:
        raise RuntimeError(f"graph sync incomplete: SQLite {counts}, Neo4j {in_neo4j}")
    state = {
        "ns": ns,
        **counts,
        "synced_at": int(time.time()),
        "seconds": round(time.time() - started, 1),
    }
    set_meta(conn, "graph_sync", state)
    conn.commit()
    return state


def drop_namespace(driver: Any, ns: str, database: str = "neo4j") -> None:
    driver.execute_query(
        "MATCH (n:Node {ns: $ns}) DETACH DELETE n", parameters_={"ns": ns}, database_=database
    )
