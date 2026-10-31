"""Graph view helpers (PS 06 bonus, PRD F8): a node's neighbourhood with provenance-labelled
edges, read through the graph store (Neo4j when synced, else SQLite), and node search."""

from __future__ import annotations

import sqlite3

import networkx as nx

from hindsight.brain.ask import label
from hindsight.brain.codegraph import CODE_KINDS
from hindsight.core.graphstore import GraphReader, SqliteGraph
from hindsight.core.schemas import GraphEdge, GraphNode, GraphResponse, SearchHit

PER_NODE = 14  # edges kept per node and kind, most recent first, so busy files stay readable


def neighbourhood(
    conn: sqlite3.Connection,
    key: str,
    depth: int = 1,
    limit: int = 80,
    graph: GraphReader | None = None,
    code: bool = False,
) -> GraphResponse | None:
    """`code` adds the code structure layer (functions, classes, calls); it is always on when
    the centre is itself a function or class."""
    graph = graph or SqliteGraph(conn)
    center = conn.execute("SELECT id, kind FROM nodes WHERE key = ?", (key,)).fetchone()
    if not center:
        return None
    hidden = frozenset() if code or center[1] == "Symbol" else CODE_KINDS
    g = nx.MultiDiGraph()
    frontier = [int(center[0])]
    seen = {int(center[0])}
    truncated = False
    for _ in range(max(1, min(depth, 2))):
        nxt: list[int] = []
        around = graph.edges_around(frontier, PER_NODE)  # one round trip per hop
        for n in frontier:
            for e in around[n]:
                if e.kind in hidden:
                    continue
                for other in (e.src, e.dst):
                    if other not in seen:
                        if len(seen) >= limit:
                            truncated = True
                            continue
                        seen.add(other)
                        nxt.append(other)
                if e.src in seen and e.dst in seen:
                    g.add_edge(
                        e.src, e.dst, key=e.kind, provenance=e.provenance, evidence=e.evidence
                    )
        frontier = nxt
    g.add_nodes_from(seen)
    marks = ",".join("?" * len(seen))
    rows = {
        r["id"]: r
        for r in conn.execute(
            f"SELECT id, kind, key, title, url, ts FROM nodes WHERE id IN ({marks})", list(seen)
        )
    }
    nodes = [
        GraphNode(
            id=rows[n]["key"],
            kind=rows[n]["kind"],
            label=label(rows[n]),
            url=rows[n]["url"],
            ts=rows[n]["ts"],
        )
        for n in g.nodes
    ]
    edges = [
        GraphEdge(
            source=rows[u]["key"],
            target=rows[v]["key"],
            kind=k,
            provenance=d["provenance"],
            evidence=d["evidence"],
        )
        for u, v, k, d in g.edges(keys=True, data=True)
    ]
    return GraphResponse(
        center=key, nodes=nodes, edges=edges, truncated=truncated, backend=graph.backend
    )


def search(conn: sqlite3.Connection, q: str, limit: int = 20) -> list[SearchHit]:
    pattern = f"%{q.strip()}%"
    rows = conn.execute(
        "SELECT kind, key, title, url FROM nodes WHERE key LIKE ? OR title LIKE ?"
        " ORDER BY CASE kind WHEN 'Service' THEN 0 WHEN 'Component' THEN 1 WHEN 'File' THEN 2"
        " WHEN 'PR' THEN 3 WHEN 'Issue' THEN 4 WHEN 'Author' THEN 5 ELSE 6 END, length(title) LIMIT ?",
        (pattern, pattern, limit),
    ).fetchall()
    return [SearchHit(key=r["key"], kind=r["kind"], label=label(r), url=r["url"]) for r in rows]
