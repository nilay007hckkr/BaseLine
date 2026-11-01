"""SQLite storage: one graph (nodes, and edges that always carry provenance and evidence),
citable text spans with FTS5 keyword search, and the commit/change tables statistics read from."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

PROVENANCE = ("explicit", "api", "static", "statistical", "runtime", "heuristic")
_PROV_SQL = ",".join(f"'{p}'" for p in PROVENANCE)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS nodes(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  ts INTEGER,
  attrs TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(attrs))
);
CREATE INDEX IF NOT EXISTS nodes_kind ON nodes(kind);

CREATE TABLE IF NOT EXISTS edges(
  id INTEGER PRIMARY KEY,
  src INTEGER NOT NULL REFERENCES nodes(id),
  dst INTEGER NOT NULL REFERENCES nodes(id),
  kind TEXT NOT NULL,
  weight REAL,
  provenance TEXT NOT NULL CHECK (provenance IN ({_PROV_SQL})),
  evidence TEXT NOT NULL
    CHECK (json_valid(evidence) AND json_type(evidence) = 'object' AND evidence <> '{{}}'),
  ts INTEGER,
  UNIQUE(src, dst, kind)
);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src, kind);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst, kind);

CREATE TABLE IF NOT EXISTS spans(
  id INTEGER PRIMARY KEY,
  node_id INTEGER NOT NULL REFERENCES nodes(id),
  span_kind TEXT NOT NULL,
  ordinal INTEGER NOT NULL DEFAULT 0,
  text TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '',
  ts INTEGER,
  flagged INTEGER NOT NULL DEFAULT 0,
  UNIQUE(node_id, span_kind, ordinal)
);
CREATE VIRTUAL TABLE IF NOT EXISTS spans_fts USING fts5(
  text, content='spans', content_rowid='id', tokenize='porter unicode61'
);
-- read-only view of the indexed words and how many texts use each (typo correction in answers)
CREATE VIRTUAL TABLE IF NOT EXISTS spans_vocab USING fts5vocab(spans_fts, 'row');
CREATE TRIGGER IF NOT EXISTS spans_ai AFTER INSERT ON spans BEGIN
  INSERT INTO spans_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS spans_ad AFTER DELETE ON spans BEGIN
  INSERT INTO spans_fts(spans_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS spans_au AFTER UPDATE ON spans BEGIN
  INSERT INTO spans_fts(spans_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO spans_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS commits(
  id INTEGER PRIMARY KEY REFERENCES nodes(id),
  sha TEXT NOT NULL UNIQUE,
  ts INTEGER NOT NULL,
  author_key TEXT NOT NULL,
  n_files INTEGER NOT NULL,
  is_bot INTEGER NOT NULL,
  is_sweep INTEGER NOT NULL,
  is_merge INTEGER NOT NULL,
  ok INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS commits_ts ON commits(ts);

CREATE TABLE IF NOT EXISTS changes(
  commit_id INTEGER NOT NULL REFERENCES commits(id),
  path TEXT NOT NULL,
  lineage TEXT NOT NULL,
  old_path TEXT,
  additions INTEGER,
  deletions INTEGER,
  generated INTEGER NOT NULL,
  PRIMARY KEY(commit_id, path)
);
CREATE INDEX IF NOT EXISTS changes_lineage ON changes(lineage, commit_id);

CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS templates(
  id TEXT NOT NULL,
  source TEXT NOT NULL,
  service TEXT NOT NULL,
  text TEXT NOT NULL,
  severity TEXT NOT NULL,
  count INTEGER NOT NULL,
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  PRIMARY KEY(id, source)
);

CREATE TABLE IF NOT EXISTS template_counts(
  win INTEGER NOT NULL,
  service TEXT NOT NULL,
  template_id TEXT NOT NULL,
  count INTEGER NOT NULL,
  source TEXT NOT NULL,
  PRIMARY KEY(win, service, template_id, source)
);

CREATE TABLE IF NOT EXISTS incidents(
  id INTEGER PRIMARY KEY,
  opened_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  closed_at INTEGER,
  source TEXT NOT NULL,
  data TEXT NOT NULL CHECK (json_valid(data)),
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS deploys(
  id INTEGER PRIMARY KEY,
  commit_sha TEXT NOT NULL,
  ts INTEGER NOT NULL,
  synthetic INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  prediction TEXT NOT NULL CHECK (json_valid(prediction)),
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_cache(
  key TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  response TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS span_vectors(
  span_id INTEGER PRIMARY KEY REFERENCES spans(id),
  model TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  vec BLOB NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS answer_checks(
  id INTEGER PRIMARY KEY,
  question TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('answered', 'not_found')),
  mode TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('supported', 'unsupported', 'right_not_found', 'should_have_answered')),
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_tests(
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'rejected', 'unvalidated')),
  reason TEXT NOT NULL DEFAULT '',
  spec TEXT NOT NULL CHECK (json_valid(spec)),
  grounding TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(grounding)),
  version INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_runs(
  id INTEGER PRIMARY KEY,
  test_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('passed', 'healed', 'failed', 'needs_human', 'flaky')),
  reason TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL CHECK (json_valid(detail)),
  started_at INTEGER NOT NULL,
  duration_ms INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_repairs(
  id INTEGER PRIMARY KEY,
  test_id TEXT NOT NULL,
  run_id INTEGER NOT NULL,
  step INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected', 'reverted')),
  data TEXT NOT NULL CHECK (json_valid(data)),
  created_at INTEGER NOT NULL,
  decided_at INTEGER
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # one connection per request or task; FastAPI may open it and use it on different threads
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)  # wait out other writers
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_node(
    conn: sqlite3.Connection,
    kind: str,
    key: str,
    title: str = "",
    url: str = "",
    ts: int | None = None,
    attrs: dict[str, Any] | None = None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO nodes(kind, key, title, url, ts, attrs) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
          kind = excluded.kind,
          title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE nodes.title END,
          url = CASE WHEN excluded.url <> '' THEN excluded.url ELSE nodes.url END,
          ts = COALESCE(excluded.ts, nodes.ts),
          attrs = json_patch(nodes.attrs, excluded.attrs)
        RETURNING id
        """,
        (kind, key, title, url, ts, json.dumps(attrs or {})),
    ).fetchone()
    return int(row[0])


def add_edge(
    conn: sqlite3.Connection,
    src: int,
    dst: int,
    kind: str,
    provenance: str,
    evidence: dict[str, Any],
    weight: float | None = None,
    ts: int | None = None,
) -> None:
    """Add or update an edge. An edge never loses evidence, and a heuristic link never
    overwrites a stronger one (explicit, api, static, statistical, runtime)."""
    if provenance not in PROVENANCE:
        raise ValueError(f"unknown provenance {provenance!r}")
    if not evidence:
        raise ValueError(f"edge {kind} has no evidence")
    conn.execute(
        """
        INSERT INTO edges(src, dst, kind, weight, provenance, evidence, ts) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(src, dst, kind) DO UPDATE SET
          provenance = excluded.provenance, evidence = excluded.evidence,
          weight = excluded.weight, ts = excluded.ts
        WHERE excluded.provenance <> 'heuristic' OR edges.provenance = 'heuristic'
        """,
        (src, dst, kind, weight, provenance, json.dumps(evidence), ts),
    )


def add_span(
    conn: sqlite3.Connection,
    node_id: int,
    span_kind: str,
    text: str,
    url: str = "",
    ts: int | None = None,
    ordinal: int = 0,
    flagged: bool = False,
) -> None:
    text = text.strip()
    if not text:
        return
    conn.execute(
        """
        INSERT INTO spans(node_id, span_kind, ordinal, text, url, ts, flagged) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(node_id, span_kind, ordinal) DO UPDATE SET
          text = excluded.text, url = excluded.url, ts = excluded.ts, flagged = excluded.flagged
        WHERE spans.text <> excluded.text OR spans.flagged <> excluded.flagged
        """,
        (node_id, span_kind, ordinal, text, url, ts, int(flagged)),
    )


def node_id(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute("SELECT id FROM nodes WHERE key = ?", (key,)).fetchone()
    return int(row[0]) if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default
