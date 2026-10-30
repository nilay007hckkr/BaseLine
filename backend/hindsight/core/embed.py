"""Semantic search over citable spans with a local embedding model (Ollama). Text and vectors never
leave this machine.

`nomic-embed-text` requires a task prefix on every input ("search_document: " for what is stored,
"search_query: " for the question; per its model card). Vectors are L2-normalised float32 in
SQLite (`span_vectors`), keyed by span and model, with a hash of the text so a changed span is
embedded again. Search is a dot product over all vectors of the model (a few thousand spans)."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx
import numpy as np

MAX_CHARS = 6000  # the model runs with a 2K-token context in Ollama; longer text is cut
DOC_PREFIX, QUERY_PREFIX = "search_document: ", "search_query: "


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    def __init__(self, host: str, model: str, timeout: float = 120) -> None:
        self.host, self.model, self.timeout = host.rstrip("/"), model, timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = httpx.post(
            f"{self.host}/api/embed",
            json={"model": self.model, "input": texts, "truncate": True},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        vectors: list[list[float]] = resp.json()["embeddings"]
        return vectors

    def available(self) -> bool:
        try:
            tags = httpx.get(f"{self.host}/api/tags", timeout=2).json()
        except (httpx.HTTPError, ValueError):
            return False
        names = {m.get("name", "") for m in tags.get("models", [])}
        return self.model in names or f"{self.model}:latest" in names


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _unit(vec: list[float]) -> bytes:
    arr = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(arr)) or 1.0
    return (arr / norm).tobytes()


def index_spans(
    conn: sqlite3.Connection,
    embedder: Embedder,
    batch: int = 32,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Embed every span that has no vector for this model or whose text changed."""
    started = time.time()
    rows = conn.execute(
        "SELECT s.id, s.text, v.text_hash, v.model FROM spans s LEFT JOIN span_vectors v ON v.span_id = s.id"
    ).fetchall()
    todo = [(r[0], r[1]) for r in rows if r[3] != embedder.model or r[2] != _hash(r[1][:MAX_CHARS])]
    for i in range(0, len(todo), batch):
        chunk = todo[i : i + batch]
        vectors = embedder.embed([DOC_PREFIX + text[:MAX_CHARS] for _, text in chunk])
        now = int(time.time())
        conn.executemany(
            "INSERT INTO span_vectors(span_id, model, text_hash, vec, created_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(span_id) DO UPDATE SET model = excluded.model, text_hash = excluded.text_hash,"
            " vec = excluded.vec, created_at = excluded.created_at",
            [
                (sid, embedder.model, _hash(text[:MAX_CHARS]), _unit(vec), now)
                for (sid, text), vec in zip(chunk, vectors, strict=True)
            ],
        )
        conn.commit()
        if (i // batch) % 20 == 0:
            log(f"  embedded {min(i + batch, len(todo))}/{len(todo)} spans")
    total = conn.execute(
        "SELECT COUNT(*) FROM span_vectors WHERE model = ?", (embedder.model,)
    ).fetchone()[0]
    return {
        "model": embedder.model,
        "embedded_now": len(todo),
        "spans_with_vectors": total,
        "seconds": round(time.time() - started, 1),
    }


_matrix_cache: dict[tuple[str, str], tuple[int, np.ndarray, list[int]]] = {}


def _matrix(conn: sqlite3.Connection, model: str) -> tuple[np.ndarray, list[int]]:
    db = conn.execute("PRAGMA database_list").fetchone()[2]
    stamp = conn.execute(
        "SELECT COUNT(*) + COALESCE(MAX(created_at), 0) FROM span_vectors WHERE model = ?", (model,)
    ).fetchone()[0]
    cached = _matrix_cache.get((db, model))
    if cached and cached[0] == stamp:
        return cached[1], cached[2]
    rows = conn.execute(
        "SELECT span_id, vec FROM span_vectors WHERE model = ? ORDER BY span_id", (model,)
    ).fetchall()
    ids = [r[0] for r in rows]
    matrix = (
        np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
        if rows
        else np.zeros((0, 1), dtype=np.float32)
    )
    _matrix_cache[(db, model)] = (stamp, matrix, ids)
    return matrix, ids


def has_index(conn: sqlite3.Connection, model: str) -> bool:
    try:
        return bool(
            conn.execute("SELECT 1 FROM span_vectors WHERE model = ? LIMIT 1", (model,)).fetchone()
        )
    except sqlite3.OperationalError:
        return False


def dense_search(
    conn: sqlite3.Connection,
    embedder: Embedder,
    query: str,
    limit: int = 50,
    allowed: Callable[[int], bool] | None = None,
) -> list[int]:
    """Span ids ranked by cosine similarity to the query. `allowed` filters candidates before
    ranking (e.g. only spans recorded before a given time)."""
    matrix, ids = _matrix(conn, embedder.model)
    if not ids:
        return []
    q = np.frombuffer(
        _unit(embedder.embed([QUERY_PREFIX + query[:MAX_CHARS]])[0]), dtype=np.float32
    )
    scores = matrix @ q
    order = np.argsort(-scores)
    out: list[int] = []
    for idx in order:
        sid = ids[int(idx)]
        if allowed is None or allowed(sid):
            out.append(sid)
            if len(out) >= limit:
                break
    return out
