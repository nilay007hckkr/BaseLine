"""Graph reads (TRD §0.2). SQLite is where ingest writes; Neo4j serves the same graph once
`graph-sync` has copied it. Every read goes through GraphReader, which speaks SQLite node ids, so
callers never care which store answered. `graph_reader()` picks Neo4j only when it is reachable and
holds exactly what SQLite holds (same node and edge counts as the last sync); otherwise SQLite.

Both implementations return the same rows in the same order (tie-breaks on ids), which the
integration parity tests check on the fixture repository."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from hindsight.core.config import Settings
from hindsight.core.db import get_meta

_TYPE = re.compile(r"^[A-Z][A-Z_]*$")  # relationship types are spliced into Cypher: validate them


@dataclass(frozen=True)
class EdgeRow:
    src: int
    dst: int
    kind: str
    provenance: str
    evidence: dict[str, Any]
    ts: int | None


@dataclass(frozen=True)
class Pair:
    src_title: str
    src_kind: str
    dst_title: str
    dst_kind: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class Mention:
    title: str
    url: str
    ts: int | None
    evidence: dict[str, Any]


class GraphReader(Protocol):
    backend: str

    def edges_around(self, nodes: Sequence[int], per_kind: int) -> dict[int, list[EdgeRow]]:
        """For each node: edges touching it, at most per_kind per edge kind, newest first."""
        ...

    def incoming(self, dsts: Sequence[int], kind: str) -> list[tuple[int, int]]:
        """(src, dst) for every `kind` edge into dsts."""
        ...

    def recent_commits(self, files: Sequence[int], limit: int) -> list[tuple[int, int]]:
        """(commit, file) for non-bot commits that modified files, newest first."""
        ...

    def step(
        self, frontier: Sequence[int], out_kinds: Sequence[str], in_kinds: Sequence[str]
    ) -> list[tuple[int, int]]:
        """(from, to) one hop on: along out_kinds edges, and backwards along in_kinds edges."""
        ...

    def pairs(self, kind: str) -> list[Pair]:
        """Every `kind` edge with its endpoint titles."""
        ...

    def mentions(self, target_keys: Sequence[str]) -> list[Mention]:
        """Doc chunks (not superseded) that mention any of target_keys."""
        ...


def _types(kinds: Sequence[str]) -> list[str]:
    for kind in kinds:
        if not _TYPE.match(kind):
            raise ValueError(f"not an edge kind: {kind!r}")
    return list(kinds)


def _marks(values: Sequence[object]) -> str:
    return ",".join("?" * len(values))


class SqliteGraph:
    backend = "sqlite"

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def edges_around(self, nodes: Sequence[int], per_kind: int) -> dict[int, list[EdgeRow]]:
        out: dict[int, list[EdgeRow]] = {}
        for node in nodes:
            rows = self.conn.execute(
                "SELECT * FROM (SELECT e.*, ROW_NUMBER() OVER (PARTITION BY e.kind"
                " ORDER BY COALESCE(e.ts, -1) DESC, e.id DESC) AS rn"
                " FROM edges e WHERE e.src = ? OR e.dst = ?) WHERE rn <= ? ORDER BY kind, rn",
                (node, node, per_kind),
            ).fetchall()
            out[node] = [
                EdgeRow(
                    r["src"],
                    r["dst"],
                    r["kind"],
                    r["provenance"],
                    json.loads(r["evidence"]),
                    r["ts"],
                )
                for r in rows
            ]
        return out

    def incoming(self, dsts: Sequence[int], kind: str) -> list[tuple[int, int]]:
        if not dsts:
            return []
        rows = self.conn.execute(
            f"SELECT src, dst FROM edges WHERE kind = ? AND dst IN ({_marks(dsts)}) ORDER BY dst, src",
            [_types([kind])[0], *dsts],
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def recent_commits(self, files: Sequence[int], limit: int) -> list[tuple[int, int]]:
        if not files:
            return []
        rows = self.conn.execute(
            f"SELECT e.src, e.dst FROM edges e JOIN commits c ON c.id = e.src"
            f" WHERE e.kind = 'MODIFIES' AND e.dst IN ({_marks(files)}) AND c.is_bot = 0"
            f" ORDER BY c.ts DESC, e.src DESC, e.dst LIMIT ?",
            [*files, limit],
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def step(
        self, frontier: Sequence[int], out_kinds: Sequence[str], in_kinds: Sequence[str]
    ) -> list[tuple[int, int]]:
        if not frontier:
            return []
        out, back = _types(out_kinds), _types(in_kinds)
        rows = self.conn.execute(
            f"SELECT src AS a, dst AS b FROM edges WHERE src IN ({_marks(frontier)})"
            f" AND kind IN ({_marks(out) or 'NULL'})"
            f" UNION SELECT dst AS a, src AS b FROM edges WHERE dst IN ({_marks(frontier)})"
            f" AND kind IN ({_marks(back) or 'NULL'}) ORDER BY a, b",
            [*frontier, *out, *frontier, *back],
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def pairs(self, kind: str) -> list[Pair]:
        rows = self.conn.execute(
            "SELECT a.title AS at, a.kind AS ak, b.title AS bt, b.kind AS bk, e.evidence FROM edges e"
            " JOIN nodes a ON a.id = e.src JOIN nodes b ON b.id = e.dst WHERE e.kind = ?"
            " ORDER BY e.src, e.dst",
            (_types([kind])[0],),
        ).fetchall()
        return [Pair(r["at"], r["ak"], r["bt"], r["bk"], json.loads(r["evidence"])) for r in rows]

    def mentions(self, target_keys: Sequence[str]) -> list[Mention]:
        if not target_keys:
            return []
        rows = self.conn.execute(
            f"SELECT ch.title, ch.url, ch.ts, e.evidence FROM edges e"
            f" JOIN nodes ch ON ch.id = e.src JOIN nodes t ON t.id = e.dst"
            f" WHERE e.kind = 'MENTIONS' AND t.key IN ({_marks(target_keys)})"
            f" AND COALESCE(json_extract(ch.attrs, '$.superseded'), 0) = 0 ORDER BY e.src, e.dst",
            list(target_keys),
        ).fetchall()
        return [Mention(r[0], r[1], r[2], json.loads(r[3])) for r in rows]


class Neo4jGraph:
    """The same reads in Cypher. Nodes carry `ns` (one namespace per repository) and `sid` (the
    SQLite id); edges carry `sid`, provenance and the evidence JSON."""

    backend = "neo4j"

    def __init__(self, driver: Any, ns: str, database: str = "neo4j") -> None:
        self.driver, self.ns, self.database = driver, ns, database

    def _run(self, query: str, **params: Any) -> list[Any]:
        records, _, _ = self.driver.execute_query(
            query, parameters_={"ns": self.ns, **params}, database_=self.database
        )
        return list(records)

    def edges_around(self, nodes: Sequence[int], per_kind: int) -> dict[int, list[EdgeRow]]:
        out: dict[int, list[EdgeRow]] = {node: [] for node in nodes}
        rows = self._run(
            "UNWIND $sids AS sid MATCH (n:Node {ns: $ns, sid: sid})-[r]-(:Node)"
            " WITH DISTINCT sid, r"
            " WITH sid, r ORDER BY coalesce(r.ts, -1) DESC, r.sid DESC"
            " WITH sid, type(r) AS kind, collect(r)[..$k] AS rs"
            " UNWIND range(0, size(rs) - 1) AS i WITH sid, kind, i, rs[i] AS r"
            " RETURN sid, startNode(r).sid AS src, endNode(r).sid AS dst, kind,"
            " r.provenance AS provenance, r.evidence AS evidence, r.ts AS ts"
            " ORDER BY sid, kind, i",
            sids=list(nodes),
            k=per_kind,
        )
        for r in rows:
            out[r["sid"]].append(
                EdgeRow(
                    r["src"],
                    r["dst"],
                    r["kind"],
                    r["provenance"],
                    json.loads(r["evidence"]),
                    r["ts"],
                )
            )
        return out

    def incoming(self, dsts: Sequence[int], kind: str) -> list[tuple[int, int]]:
        if not dsts:
            return []
        rows = self._run(
            f"MATCH (a:Node)-[:{_types([kind])[0]}]->(b:Node {{ns: $ns}}) WHERE b.sid IN $dsts"
            " RETURN a.sid AS src, b.sid AS dst ORDER BY dst, src",
            dsts=list(dsts),
        )
        return [(r["src"], r["dst"]) for r in rows]

    def recent_commits(self, files: Sequence[int], limit: int) -> list[tuple[int, int]]:
        if not files:
            return []
        rows = self._run(
            "MATCH (c:Node {ns: $ns})-[:MODIFIES]->(f:Node {ns: $ns})"
            " WHERE f.sid IN $files AND c.is_bot = false"
            " RETURN c.sid AS cid, f.sid AS fid ORDER BY c.ts DESC, cid DESC, fid LIMIT $limit",
            files=list(files),
            limit=limit,
        )
        return [(r["cid"], r["fid"]) for r in rows]

    def step(
        self, frontier: Sequence[int], out_kinds: Sequence[str], in_kinds: Sequence[str]
    ) -> list[tuple[int, int]]:
        if not frontier:
            return []
        found: set[tuple[int, int]] = set()
        if out_kinds:
            for r in self._run(
                f"MATCH (n:Node {{ns: $ns}})-[:{'|'.join(_types(out_kinds))}]->(m:Node)"
                " WHERE n.sid IN $frontier RETURN n.sid AS a, m.sid AS b",
                frontier=list(frontier),
            ):
                found.add((r["a"], r["b"]))
        if in_kinds:
            for r in self._run(
                f"MATCH (n:Node {{ns: $ns}})<-[:{'|'.join(_types(in_kinds))}]-(m:Node)"
                " WHERE n.sid IN $frontier RETURN n.sid AS a, m.sid AS b",
                frontier=list(frontier),
            ):
                found.add((r["a"], r["b"]))
        return sorted(found)

    def pairs(self, kind: str) -> list[Pair]:
        rows = self._run(
            f"MATCH (a:Node {{ns: $ns}})-[r:{_types([kind])[0]}]->(b:Node)"
            " RETURN a.title AS at, a.kind AS ak, b.title AS bt, b.kind AS bk, r.evidence AS ev"
            " ORDER BY a.sid, b.sid"
        )
        return [Pair(r["at"], r["ak"], r["bt"], r["bk"], json.loads(r["ev"])) for r in rows]

    def mentions(self, target_keys: Sequence[str]) -> list[Mention]:
        if not target_keys:
            return []
        rows = self._run(
            "MATCH (ch:Node {ns: $ns})-[r:MENTIONS]->(t:Node {ns: $ns})"
            " WHERE t.key IN $keys AND coalesce(ch.superseded, false) = false"
            " RETURN ch.title AS title, ch.url AS url, ch.ts AS ts, r.evidence AS ev"
            " ORDER BY ch.sid, t.sid",
            keys=list(target_keys),
        )
        return [Mention(r["title"], r["url"], r["ts"], json.loads(r["ev"])) for r in rows]


# ---------- choosing the store ----------

_driver_cache: dict[tuple[str, str], tuple[float, Any]] = {}
_RECHECK_S = 30.0


def neo4j_driver(settings: Settings) -> Any | None:
    """A connected driver, or None when Neo4j is not running or the password is not set. The
    answer is cached for 30 s so an absent Neo4j costs one failed connection, not one per request."""
    if not settings.neo4j_password:
        return None
    cache_key = (settings.neo4j_uri, settings.neo4j_user)
    checked = _driver_cache.get(cache_key)
    if checked and time.monotonic() - checked[0] < _RECHECK_S:
        return checked[1]
    from neo4j import GraphDatabase
    from neo4j.exceptions import AuthError, ServiceUnavailable

    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        connection_timeout=2.0,
    )
    try:
        driver.verify_connectivity()
    except (ServiceUnavailable, AuthError, OSError):
        driver.close()
        driver = None
    old = _driver_cache.get(cache_key)
    if old and old[1] is not None and old[1] is not driver:
        old[1].close()
    _driver_cache[cache_key] = (time.monotonic(), driver)
    return driver


def namespace(conn: sqlite3.Connection) -> str:
    repo = get_meta(conn, "repo") or {}
    return f"{repo.get('owner', 'local')}__{repo.get('name', 'graph')}"


def graph_counts(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT (SELECT COUNT(*) FROM nodes), (SELECT COUNT(*) FROM edges),"
        " (SELECT COALESCE(MAX(id), 0) FROM nodes), (SELECT COALESCE(MAX(id), 0) FROM edges)"
    ).fetchone()
    return {"nodes": row[0], "edges": row[1], "max_node": row[2], "max_edge": row[3]}


class GraphUnavailableError(RuntimeError):
    pass


def graph_reader(conn: sqlite3.Connection, settings: Settings) -> GraphReader:
    if settings.graph_backend == "sqlite":
        return SqliteGraph(conn)
    synced = get_meta(conn, "graph_sync") or {}
    in_sync = synced.get("ns") == namespace(conn) and all(
        synced.get(k) == v for k, v in graph_counts(conn).items()
    )
    driver = neo4j_driver(settings) if in_sync or settings.graph_backend == "neo4j" else None
    if driver is not None and in_sync:
        return Neo4jGraph(driver, synced["ns"])
    if settings.graph_backend == "neo4j":
        reason = (
            "not reachable" if driver is None else "not in sync with SQLite (run make graph-sync)"
        )
        raise GraphUnavailableError(f"GRAPH_BACKEND=neo4j but Neo4j is {reason}")
    return SqliteGraph(conn)
