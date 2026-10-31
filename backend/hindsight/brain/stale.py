"""Docs that may be out of date: the code a doc talks about kept changing after the doc's last edit.

A doc's sections MENTION files and services (brain/uploads.py link_chunk). When at least
MIN_CHANGES commits by people (bot, sweep and merge commits left out) that are mainly about those
files, or the files of those services (at least half of what each commit changed), landed after
the doc was last changed, the doc is flagged "may be out of date",
with the count, the latest commits and the names as evidence. A heuristic signal from recorded
history (provenance statistical), never a verdict: the doc may still be right."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from hindsight.core.db import get_meta
from hindsight.core.schemas import ImpactItem, Reason

MIN_CHANGES = 3
SHOWN_COMMITS = 3


def _date(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


def doc_name(title: str) -> str:
    """'README.md § Architecture' or 'runbook.md@v2 § Steps' → the doc's own name."""
    return title.split(" § ", 1)[0].split("@v", 1)[0]


def stale_docs(conn: sqlite3.Connection, min_changes: int = MIN_CHANGES) -> list[ImpactItem]:
    web = (get_meta(conn, "repo", {}) or {}).get("web", "")
    out: list[ImpactItem] = []
    docs = conn.execute(
        "SELECT id, title, url, ts FROM nodes WHERE kind = 'Doc' AND ts IS NOT NULL"
        " AND COALESCE(json_extract(attrs, '$.superseded'), 0) = 0"
    ).fetchall()
    for doc in docs:
        targets = conn.execute(
            "SELECT DISTINCT t.id, t.kind, t.title FROM edges p"
            " JOIN edges m ON m.src = p.src AND m.kind = 'MENTIONS'"
            " JOIN nodes t ON t.id = m.dst"
            " WHERE p.kind = 'PART_OF' AND p.dst = ? AND t.kind IN ('File', 'Service', 'Component')",
            (doc["id"],),
        ).fetchall()
        if not targets:
            continue
        files: set[int] = set()
        for t in targets:
            if t["kind"] == "File":
                files.add(t["id"])
            else:
                files |= {
                    r[0]
                    for r in conn.execute(
                        "SELECT src FROM edges WHERE kind = 'BELONGS_TO' AND dst = ?", (t["id"],)
                    )
                }
        if not files:
            continue
        marks = ",".join("?" * len(files))
        # only commits mainly about what the doc describes: at least half of the files they
        # changed (a release or dependency bump that touches every service does not count)
        commits = conn.execute(
            "SELECT c.id, c.sha, c.ts, n.title FROM edges e"
            " JOIN commits c ON c.id = e.src JOIN nodes n ON n.id = c.id"
            f" WHERE e.kind = 'MODIFIES' AND e.dst IN ({marks}) AND c.ok = 1 AND c.ts > ?"
            " GROUP BY c.id HAVING 2 * COUNT(DISTINCT e.dst) >= MAX(c.n_files, 1)"
            " ORDER BY c.ts DESC",
            [*files, doc["ts"]],
        ).fetchall()
        if len(commits) < min_changes:
            continue
        names = sorted({t["title"] for t in targets})
        shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
        reasons = [
            Reason(
                text=f"last edited {_date(doc['ts'])}; since then {len(commits)} commits changed"
                f" what it describes ({shown})",
                provenance="statistical",
                url=doc["url"] or "",
            )
        ]
        for c in commits[:SHOWN_COMMITS]:
            reasons.append(
                Reason(
                    text=f"commit {c['sha'][:7]} ({_date(c['ts'])}): {c['title']}",
                    provenance="explicit",
                    url=f"{web}/commit/{c['sha']}" if web else "",
                )
            )
        out.append(
            ImpactItem(
                target_kind="doc",
                target=doc["title"],
                score=round(min(1.0, len(commits) / 20), 3),
                reasons=reasons,
                url=doc["url"] or "",
            )
        )
    return sorted(out, key=lambda i: (-i.score, i.target))


def stale_by_name(conn: sqlite3.Connection) -> dict[str, ImpactItem]:
    return {doc_name(item.target): item for item in stale_docs(conn)}
