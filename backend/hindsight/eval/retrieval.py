"""Retrieval evaluation for the "why" answers (automatic proxy; no hand-written question set).

Task: given only the title of a pull request or commit, find the issue it links to. The answer key
is the graph's own links:
- `fixes`: explicit "Fixes #N" links (provenance explicit);
- `references`: plain "#N" mentions from a PR to an issue (provenance heuristic; weaker key).
The "#N" text is removed from the query. Candidates are only spans recorded at or before the
query's time (no leakage), and never the query's own spans. A query counts as found at rank k when
any span of the linked issue is in the top k.

Methods: keyword (FTS5 BM25, as in the answers), semantic (local embeddings), and hybrid (Reciprocal
Rank Fusion of both, as in the answers). 95% bootstrap CIs, seed 13."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from hindsight.core.db import get_meta
from hindsight.core.embed import Embedder, dense_search
from hindsight.core.text import content_terms, fts_term
from hindsight.eval.rca import _ci as bootstrap_ci

RRF_K, DEPTH = 60, 50


def queries(conn: sqlite3.Connection, key: str, limit: int = 300) -> list[dict[str, Any]]:
    kind, prov = {"fixes": ("FIXES", "explicit"), "references": ("REFERENCES", "heuristic")}[key]
    rows = conn.execute(
        "SELECT a.id AS src, a.title AS title, a.ts AS ts, b.id AS dst FROM edges e"
        " JOIN nodes a ON a.id = e.src JOIN nodes b ON b.id = e.dst"
        " WHERE e.kind = ? AND e.provenance = ? AND b.kind = 'Issue' AND a.kind IN ('PR', 'Commit')"
        " AND a.ts IS NOT NULL AND EXISTS (SELECT 1 FROM spans s WHERE s.node_id = b.id AND s.ts <= a.ts)"
        " ORDER BY a.ts, a.id LIMIT ?",
        (kind, prov, limit),
    ).fetchall()
    out = []
    for r in rows:
        text = re.sub(r"\(?#\d+\)?", " ", r["title"] or "").strip()
        if len(content_terms(text)) >= 2:
            out.append({"src": r["src"], "dst": r["dst"], "ts": r["ts"], "query": text})
    return out


def _keyword(conn: sqlite3.Connection, q: dict[str, Any]) -> list[int]:
    terms = content_terms(q["query"])
    match = " OR ".join(fts_term(t) for t in terms)
    rows = conn.execute(
        "SELECT s.id FROM spans_fts f JOIN spans s ON s.id = f.rowid WHERE spans_fts MATCH ?"
        " AND s.ts <= ? AND s.node_id <> ? ORDER BY bm25(spans_fts) LIMIT ?",
        (match, q["ts"], q["src"], DEPTH),
    ).fetchall()
    return [r[0] for r in rows]


def _rrf(*lists: list[int]) -> list[int]:
    score: dict[int, float] = defaultdict(float)
    for ranked in lists:
        for i, span in enumerate(ranked):
            score[span] += 1.0 / (RRF_K + i + 1)
    return [s for s, _ in sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))]


def _rank(ranked: list[int], targets: set[int]) -> int | None:
    return next((i + 1 for i, s in enumerate(ranked) if s in targets), None)


def run(
    conn: sqlite3.Connection, embedder: Embedder, out_dir: Path | None = None
) -> dict[str, Any]:
    spans = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, node_id, ts FROM spans")}
    result: dict[str, Any] = {
        "name": "retrieval_issue_recovery",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "command": "make eval-retrieval",
        "repo": get_meta(conn, "repo", {}),
        "embed_model": embedder.model,
        "sets": {},
    }
    for key in ("fixes", "references"):
        qs = queries(conn, key)
        ranks: dict[str, list[int | None]] = {"keyword": [], "semantic": [], "hybrid": []}
        for q in qs:
            targets = {sid for sid, (node, _) in spans.items() if node == q["dst"]}

            def allowed(sid: int, q: dict[str, Any] = q) -> bool:
                node, ts = spans.get(sid, (None, None))
                return node is not None and node != q["src"] and ts is not None and ts <= q["ts"]

            keyword = _keyword(conn, q)
            semantic = dense_search(conn, embedder, q["query"], DEPTH, allowed)
            hybrid = _rrf(keyword, semantic)
            for name, ranked in (("keyword", keyword), ("semantic", semantic), ("hybrid", hybrid)):
                ranks[name].append(_rank(ranked, targets))
        methods = {}
        for name, rs in ranks.items():
            methods[name] = {
                "Hit@1": bootstrap_ci([1.0 if r is not None and r <= 1 else 0.0 for r in rs]),
                "Hit@5": bootstrap_ci([1.0 if r is not None and r <= 5 else 0.0 for r in rs]),
                "Hit@10": bootstrap_ci([1.0 if r is not None and r <= 10 else 0.0 for r in rs]),
                "MRR": bootstrap_ci([1.0 / r if r else 0.0 for r in rs]),
            }
        result["sets"][key] = {"n_queries": len(qs), "methods": methods}
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"retrieval-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
        path.write_text(json.dumps(result, indent=2))
        result["result_file"] = str(path)
    return result
