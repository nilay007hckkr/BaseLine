"""Reading incidents back: ranking, patterns, timeline lanes, candidate changes, runbooks and a short
summary. The summary is written by code from the ranking and cites pattern ids; no model decides
anything here."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from hindsight.brain.uploads import runbooks_for
from hindsight.core.schemas import (
    ChangeCandidate,
    CulpritRank,
    Incident,
    IncidentSummary,
    IncidentTemplate,
    RunbookSection,
    TimelinePoint,
)
from hindsight.correlate.deploys import candidates as change_candidates


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%H:%M:%S UTC")


def culprit_of(data: dict[str, Any]) -> str | None:
    return next((r["service"] for r in data.get("ranking", []) if r["role"] == "culprit"), None)


def summarise(
    ranking: list[CulpritRank],
    cands: list[ChangeCandidate],
    runbooks: list[RunbookSection],
    opened_at: int,
) -> str:
    culprit = next((r for r in ranking if r.role == "culprit"), None)
    if culprit is None:
        return "No culprit could be ranked from the available logs."
    parts = [f"Opened {_clock(opened_at)}. Likely culprit: {culprit.service}."]
    named_by = culprit.evidence.get("named_by", [])
    if named_by:
        who = ", ".join(f"{n['service']} ({n['errors']} errors)" for n in named_by)
        tids = sorted({t for r in ranking if r.role == "victim" for t in r.templates})
        parts.append(
            f"Error messages from {who} name {culprit.service}"
            + (f" [{', '.join(tids)}]." if tids else ".")
        )
    if culprit.templates:
        parts.append(f"{culprit.service}'s own anomalous patterns: {', '.join(culprit.templates)}.")
    elif named_by:
        parts.append(
            f"{culprit.service} itself logged nothing unusual, which fits it being down or unreachable."
        )
    victims = [r.service for r in ranking if r.role == "victim"]
    if victims:
        parts.append(f"Victims (failing because of it): {', '.join(victims)}.")
    if cands:
        top = cands[0]
        gap = top.evidence.get("gap_s", 0)
        parts.append(
            f'Top candidate change: {top.commit[:7]} "{top.title}", deployed {gap // 60} min earlier '
            f"(score {top.score:.2f}; a correlation, not proof)."
        )
    if runbooks:
        parts.append(f"Runbook: {runbooks[0].title}.")
    return " ".join(parts)


def _timeline(
    conn: sqlite3.Connection, source: str, start: int, end: int
) -> dict[str, list[TimelinePoint]]:
    rows = conn.execute(
        "SELECT tc.win, tc.service, SUM(tc.count) AS n,"
        " SUM(CASE WHEN t.severity = 'error' THEN tc.count ELSE 0 END) AS errors"
        " FROM template_counts tc LEFT JOIN templates t ON t.id = tc.template_id AND t.source = tc.source"
        " WHERE tc.source = ? AND tc.win BETWEEN ? AND ? GROUP BY tc.win, tc.service ORDER BY tc.win",
        (source, start, end),
    ).fetchall()
    lanes: dict[str, list[TimelinePoint]] = defaultdict(list)
    for r in rows:
        lanes[r["service"]].append(TimelinePoint(ts=r["win"], count=r["n"], errors=r["errors"]))
    return dict(lanes)


def list_incidents(conn: sqlite3.Connection, limit: int = 50) -> list[IncidentSummary]:
    rows = conn.execute(
        "SELECT * FROM incidents ORDER BY opened_at DESC LIMIT ?", (limit,)
    ).fetchall()
    out = []
    for r in rows:
        data = json.loads(r["data"])
        out.append(
            IncidentSummary(
                id=r["id"],
                opened_at=r["opened_at"],
                updated_at=r["updated_at"],
                closed_at=r["closed_at"],
                source=r["source"],
                culprit=culprit_of(data),
                services=[x["service"] for x in data.get("ranking", [])],
            )
        )
    return out


def get_incident(conn: sqlite3.Connection, incident_id: int) -> Incident | None:
    r = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    if r is None:
        return None
    data = json.loads(r["data"])
    ranking = [CulpritRank(**x) for x in data.get("ranking", [])]
    culprit = culprit_of(data)
    samples = " ".join(s for x in ranking for s in x.samples)
    cands = change_candidates(conn, r["opened_at"], culprit, samples)
    runbooks = runbooks_for(conn, culprit) if culprit else []
    end = (r["closed_at"] or r["updated_at"]) + 60
    return Incident(
        id=r["id"],
        opened_at=r["opened_at"],
        updated_at=r["updated_at"],
        closed_at=r["closed_at"],
        source=r["source"],
        ranking=ranking,
        templates=[IncidentTemplate(**t) for t in data.get("templates", [])],
        anomalies=data.get("anomalies", []),
        candidates=cands,
        runbooks=runbooks,
        timeline=_timeline(conn, r["source"], r["opened_at"] - 300, end),
        summary=summarise(ranking, cands, runbooks, r["opened_at"]),
    )
