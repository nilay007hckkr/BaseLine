"""Deploys and incident → change candidates (DESIGN §7).

A deploy records a commit and a time. When it is recorded, the impact report for that commit
(computed from history before it) is stored as the deploy's prediction; correlation later reads that
stored prediction, never a fresh one. Candidates are deploys in the 24 hours before an incident,
scored on time proximity, whether the commit touches the culprit service, whether the culprit was in
the stored prediction, and whether a changed file's name appears in the incident's log samples.
Results are always labelled candidates: this is correlation, not proof."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path

from hindsight.brain.impact import History, impact_report, resolve_commit
from hindsight.core.db import get_meta
from hindsight.core.schemas import ChangeCandidate, DeployRecord

LOOKBACK = 24 * 3600
WEIGHTS = {"time": 0.4, "touches_culprit": 0.3, "culprit_in_prediction": 0.2, "file_in_logs": 0.1}


def _title(conn: sqlite3.Connection, sha: str) -> str:
    row = conn.execute("SELECT title FROM nodes WHERE key = ?", (f"commit:{sha}",)).fetchone()
    return row[0] if row else ""


def _record(conn: sqlite3.Connection, row: sqlite3.Row) -> DeployRecord:
    prediction = json.loads(row["prediction"])
    return DeployRecord(
        id=row["id"],
        commit=row["commit_sha"],
        title=_title(conn, row["commit_sha"]),
        ts=row["ts"],
        synthetic=bool(row["synthetic"]),
        note=row["note"],
        predicted_components=prediction.get("components", []),
    )


def record_deploy(
    conn: sqlite3.Connection,
    commit: str,
    ts: int | None = None,
    synthetic: bool = False,
    note: str = "",
) -> DeployRecord | None:
    resolved = resolve_commit(conn, commit)
    if resolved is None:
        return None
    sha, _, files = resolved
    report = impact_report(conn, commit=sha)
    prediction = {"components": [c.target for c in report.components], "files": files}
    cur = conn.execute(
        "INSERT INTO deploys(commit_sha, ts, synthetic, note, prediction, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            sha,
            ts or int(time.time()),
            int(synthetic),
            note,
            json.dumps(prediction),
            int(time.time()),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM deploys WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _record(conn, row)


def list_deploys(conn: sqlite3.Connection, limit: int = 50) -> list[DeployRecord]:
    rows = conn.execute("SELECT * FROM deploys ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [_record(conn, r) for r in rows]


def candidates(
    conn: sqlite3.Connection, opened_at: int, culprit: str | None, samples: str
) -> list[ChangeCandidate]:
    web = (get_meta(conn, "repo", {}) or {}).get("web", "")
    rows = conn.execute(
        "SELECT * FROM deploys WHERE ts <= ? AND ts >= ? ORDER BY ts DESC",
        (opened_at, opened_at - LOOKBACK),
    ).fetchall()
    history = History(conn)
    lowered = samples.lower()
    best: dict[str, ChangeCandidate] = {}
    for row in rows:
        sha = row["commit_sha"]
        prediction = json.loads(row["prediction"])
        files = prediction.get("files", [])
        gap = max(0, opened_at - row["ts"])
        touches = bool(culprit) and any(history.component(f) == ("Service", culprit) for f in files)
        in_prediction = bool(culprit) and culprit in prediction.get("components", [])
        named = [
            Path(f).name for f in files if len(Path(f).name) > 4 and Path(f).name.lower() in lowered
        ]
        score = (
            WEIGHTS["time"] * math.exp(-gap / 600)
            + WEIGHTS["touches_culprit"] * touches
            + WEIGHTS["culprit_in_prediction"] * in_prediction
            + WEIGHTS["file_in_logs"] * bool(named)
        )
        candidate = ChangeCandidate(
            commit=sha,
            title=_title(conn, sha),
            url=f"{web}/commit/{sha}" if web else "",
            score=round(score, 3),
            deployed_at=row["ts"],
            synthetic=bool(row["synthetic"]),
            evidence={
                "gap_s": gap,
                "touches_culprit": touches,
                "culprit_in_stored_impact_prediction": in_prediction,
                "changed_files_named_in_logs": named,
                "weights": WEIGHTS,
            },
        )
        if sha not in best or best[sha].score < candidate.score:
            best[sha] = candidate
    return sorted(best.values(), key=lambda c: -c.score)
