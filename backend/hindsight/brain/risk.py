"""Knowledge at risk (PS 06 bonus, PRD F9): code that only one person has worked on, and whether
that person is still active. Bot, sweep and merge commits are excluded."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from hindsight.brain.impact import History
from hindsight.core.db import get_meta
from hindsight.core.schemas import RiskComponent, RiskFile, RiskReport

DAY = 86400


def knowledge_at_risk(
    conn: sqlite3.Connection,
    min_changes: int = 3,
    min_share: float = 0.8,
    inactive_after_days: int = 365,
    limit: int = 100,
) -> RiskReport:
    web = (get_meta(conn, "repo", {}) or {}).get("web", "")
    names = dict(conn.execute("SELECT key, title FROM nodes WHERE kind = 'Author'").fetchall())
    last_active = dict(
        conn.execute(
            "SELECT author_key, MAX(ts) FROM commits WHERE is_bot = 0 GROUP BY author_key"
        ).fetchall()
    )
    repo_last = int(conn.execute("SELECT COALESCE(MAX(ts), 0) FROM commits").fetchone()[0])
    at_head = {
        r[0]
        for r in conn.execute(
            "SELECT title FROM nodes WHERE kind = 'File' AND json_extract(attrs, '$.at_head') = 1"
        )
    }
    per_file: dict[str, dict[str, int]] = defaultdict(dict)
    for f, author, n in conn.execute(
        "SELECT ch.lineage, c.author_key, COUNT(DISTINCT c.id) FROM changes ch"
        " JOIN commits c ON c.id = ch.commit_id AND c.ok = 1 WHERE ch.generated = 0"
        " GROUP BY ch.lineage, c.author_key"
    ):
        per_file[f][author] = int(n)

    history = History(conn)
    files: list[RiskFile] = []
    per_component: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for path, authors in per_file.items():
        if path not in at_head:
            continue
        _, component = history.component(path)
        for a, n in authors.items():
            per_component[component][a] += n
        total = sum(authors.values())
        top, top_n = max(authors.items(), key=lambda kv: kv[1])
        share = top_n / total
        if total < min_changes or share < min_share:
            continue
        last = int(last_active.get(top, 0))
        files.append(
            RiskFile(
                path=path,
                component=component,
                changes=total,
                authors=len(authors),
                top_author=names.get(top, top),
                top_share=round(share, 3),
                single_owner=len(authors) == 1,
                top_author_last_active_ts=last,
                top_author_active=repo_last - last <= inactive_after_days * DAY,
                url=f"{web}/blob/HEAD/{path}" if web else "",
            )
        )
    files.sort(key=lambda f: (not f.single_owner, f.top_author_active, -f.changes, f.path))

    components: list[RiskComponent] = []
    for name, authors in per_component.items():
        total = sum(authors.values())
        ranked = sorted(authors.values(), reverse=True)
        covered, bus = 0, 0
        for n in ranked:
            covered += n
            bus += 1
            if covered * 2 >= total:
                break
        top, top_n = max(authors.items(), key=lambda kv: kv[1])
        components.append(
            RiskComponent(
                component=name,
                changes=total,
                authors=len(authors),
                bus_factor=bus,
                top_author=names.get(top, top),
                top_share=round(top_n / total, 3),
            )
        )
    components.sort(key=lambda c: (c.bus_factor, -c.top_share, c.component))
    return RiskReport(
        files=files[:limit],
        components=components,
        min_changes=min_changes,
        inactive_after_days=inactive_after_days,
    )
