"""Blast radius (DESIGN §2): co-change + static service calls + same component + recency, rolled
up to components, tests, docs and people. Every reason is filled from edge evidence; no LLM writes
reasons. Statistics only use commits strictly before `as_of` when one is given (no leakage)."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from hindsight.core.db import get_meta
from hindsight.core.graphstore import GraphReader, SqliteGraph
from hindsight.core.schemas import ImpactItem, ImpactReport, Reason

HALF_LIFE_DAYS = 365.0
TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec)/|_test\.(go|py)$|(^|/)test_[^/]+\.py$|\.(test|spec)\.[jt]sx?$"
    r"|Tests?\.(cs|java)$",
    re.IGNORECASE,
)
_FAR_FUTURE = 2**62


@dataclass(frozen=True)
class Weights:
    cochange: float = 0.45
    static: float = 0.25
    same_component: float = 0.15
    recency: float = 0.15


def _date(ts: int | None) -> str:
    return datetime.fromtimestamp(ts, UTC).date().isoformat() if ts else "?"


def is_test(path: str) -> bool:
    return bool(TEST_PATH.search(path))


class History:
    """As-of views over the changes table (filtered commits only, generated files excluded)."""

    def __init__(
        self, conn: sqlite3.Connection, as_of: int | None = None, graph: GraphReader | None = None
    ) -> None:
        self.conn = conn
        self.graph = graph or SqliteGraph(conn)  # structure (components, calls); history stays SQL
        self.t = as_of if as_of is not None else _FAR_FUTURE
        self._components: dict[str, tuple[str, str]] | None = None
        self._calls: dict[tuple[str, str], dict[str, object]] | None = None

    def count(self, path: str) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(DISTINCT ch.commit_id) FROM changes ch JOIN commits c ON c.id = ch.commit_id"
                " AND c.ok = 1 AND c.ts < ? WHERE ch.lineage = ? AND ch.generated = 0",
                (self.t, path),
            ).fetchone()[0]
        )

    def partners(self, path: str) -> dict[str, tuple[int, int]]:
        rows = self.conn.execute(
            "SELECT b.lineage AS f, COUNT(DISTINCT c.id) AS n, MAX(c.ts) AS last_ts FROM changes a"
            " JOIN commits c ON c.id = a.commit_id AND c.ok = 1 AND c.ts < ?"
            " JOIN changes b ON b.commit_id = a.commit_id AND b.lineage <> a.lineage AND b.generated = 0"
            " WHERE a.lineage = ? AND a.generated = 0 GROUP BY b.lineage HAVING n >= 2",
            (self.t, path),
        ).fetchall()
        return {r["f"]: (int(r["n"]), int(r["last_ts"])) for r in rows}

    def last_shared_commit(self, a: str, b: str) -> str | None:
        row = self.conn.execute(
            "SELECT c.sha FROM changes x JOIN changes y ON y.commit_id = x.commit_id"
            " JOIN commits c ON c.id = x.commit_id AND c.ok = 1 AND c.ts < ?"
            " WHERE x.lineage = ? AND y.lineage = ? ORDER BY c.ts DESC LIMIT 1",
            (self.t, a, b),
        ).fetchone()
        return row[0] if row else None

    def active_files(self) -> dict[str, tuple[int, int]]:
        """path → (changes, last change ts) over filtered commits before as_of."""
        rows = self.conn.execute(
            "SELECT ch.lineage AS f, COUNT(DISTINCT c.id) AS n, MAX(c.ts) AS last_ts FROM changes ch"
            " JOIN commits c ON c.id = ch.commit_id AND c.ok = 1 AND c.ts < ? WHERE ch.generated = 0"
            " GROUP BY ch.lineage",
            (self.t,),
        ).fetchall()
        return {r["f"]: (int(r["n"]), int(r["last_ts"])) for r in rows}

    def components(self) -> dict[str, tuple[str, str]]:
        if self._components is None:
            self._components = {
                p.src_title: (p.dst_kind, p.dst_title) for p in self.graph.pairs("BELONGS_TO")
            }
        return self._components

    def component(self, path: str) -> tuple[str, str]:
        return self.components().get(
            path, ("Component", path.split("/")[0] if "/" in path else "(root)")
        )

    def calls(self) -> dict[tuple[str, str], dict[str, object]]:
        if self._calls is None:
            self._calls = {
                (p.src_title, p.dst_title): p.evidence for p in self.graph.pairs("CALLS")
            }
        return self._calls


@dataclass
class Scored:
    path: str
    score: float
    parts: dict[str, float]
    reasons: list[Reason]


def _service_distance(
    history: History, changed_services: set[str]
) -> dict[str, tuple[int, str, str]]:
    """service → (hops, changed service it connects to, relation) over the static call graph."""
    calls = history.calls()
    neighbours: dict[str, set[str]] = {}
    for a, b in calls:
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    result: dict[str, tuple[int, str, str]] = {}
    for s in changed_services:
        for n in neighbours.get(s, set()):
            if n in changed_services:
                continue
            relation = "calls" if (n, s) in calls else "is called by"
            if n not in result or result[n][0] > 1:
                result[n] = (1, s, relation)
            for m in neighbours.get(n, set()):
                if m not in changed_services and m not in result:
                    result[m] = (2, s, "is two calls away from")
    return result


def rank_files(
    history: History,
    changed: list[str],
    weights: Weights,
    now_ts: int,
    web: str = "",
    limit: int = 25,
) -> list[Scored]:
    changed_set = set(changed)
    changed_components = {history.component(c) for c in changed}
    changed_services = {name for kind, name in changed_components if kind == "Service"}
    distance = _service_distance(history, changed_services)
    calls = history.calls()

    best_conf: dict[str, tuple[float, str, int, int, int]] = {}
    for c in changed:
        n_c = history.count(c)
        if n_c == 0:
            continue
        for f, (n_cf, last_ts) in history.partners(c).items():
            if f in changed_set:
                continue
            conf = n_cf / n_c
            if f not in best_conf or conf > best_conf[f][0]:
                best_conf[f] = (conf, c, n_cf, n_c, last_ts)

    candidates = set(best_conf)
    active = history.active_files()
    same = sorted(
        (f for f in active if f not in changed_set and history.component(f) in changed_components),
        key=lambda f: -active[f][0],
    )[:60]
    candidates.update(same)

    scored: list[Scored] = []
    for f in candidates:
        reasons: list[Reason] = []
        parts = {"cochange": 0.0, "static": 0.0, "same_component": 0.0, "recency": 0.0}
        if f in best_conf:
            conf, c, n_cf, n_c, last_ts = best_conf[f]
            parts["cochange"] = conf
            age_days = max(0.0, (now_ts - last_ts) / 86400)
            parts["recency"] = 0.5 ** (age_days / HALF_LIFE_DAYS)
            sha = history.last_shared_commit(c, f)
            reasons.append(
                Reason(
                    text=f"changed together with {c} in {n_cf} of {n_c} commits"
                    + (f" (last {sha[:7]}, {_date(last_ts)})" if sha else ""),
                    provenance="statistical",
                    url=f"{web}/commit/{sha}" if web and sha else "",
                )
            )
        kind, comp = history.component(f)
        if (kind, comp) in changed_components:
            parts["same_component"] = 1.0
            reasons.append(
                Reason(
                    text=f"in the same {kind.lower()} ({comp}) as the change",
                    provenance="heuristic",
                )
            )
        elif kind == "Service" and comp in distance:
            hops, other, relation = distance[comp]
            parts["static"] = 1.0 if hops == 1 else 0.5
            ev = calls.get((comp, other)) or calls.get((other, comp)) or {}
            where = f" ({ev.get('file')}:{ev.get('line')})" if ev else ""
            reasons.append(
                Reason(
                    text=f"{comp} {relation} {other}{where}",
                    provenance="static",
                    url=f"{web}/blob/HEAD/{ev.get('file')}#L{ev.get('line')}" if web and ev else "",
                )
            )
        score = (
            weights.cochange * parts["cochange"]
            + weights.static * parts["static"]
            + weights.same_component * parts["same_component"]
            + weights.recency * parts["recency"]
        )
        scored.append(Scored(f, round(score, 4), parts, reasons))
    scored.sort(key=lambda s: (-s.score, -active.get(s.path, (0, 0))[0], s.path))
    return scored[:limit]


def _components(
    history: History, changed: list[str], ranked: list[Scored], weights: Weights, web: str
) -> list[ImpactItem]:
    items: dict[str, ImpactItem] = {}
    for c in changed:
        kind, name = history.component(c)
        item = items.setdefault(
            name,
            ImpactItem(
                target_kind="service" if kind == "Service" else "component",
                target=name,
                score=1.0,
                reasons=[],
            ),
        )
        item.reasons.append(Reason(text=f"contains changed file {c}", provenance="explicit"))
    for s in ranked:
        kind, name = history.component(s.path)
        if name in items and items[name].score >= s.score:
            continue
        best = s.reasons[0] if s.reasons else Reason(text="", provenance="heuristic")
        items[name] = ImpactItem(
            target_kind="service" if kind == "Service" else "component",
            target=name,
            score=s.score,
            reasons=[
                Reason(text=f"via {s.path}: {best.text}", provenance=best.provenance, url=best.url)
            ],
        )
    changed_services = {
        name for kind, name in (history.component(c) for c in changed) if kind == "Service"
    }
    calls = history.calls()
    for name, (hops, other, relation) in _service_distance(history, changed_services).items():
        if hops != 1:
            continue
        ev = calls.get((name, other)) or calls.get((other, name)) or {}
        reason = Reason(
            text=f"{name} {relation} {other} ({ev.get('file')}:{ev.get('line')})",
            provenance="static",
            url=f"{web}/blob/HEAD/{ev.get('file')}#L{ev.get('line')}" if web and ev else "",
        )
        if name in items:
            if all(r.provenance != "static" for r in items[name].reasons):
                items[name].reasons.append(reason)
            continue
        items[name] = ImpactItem(
            target_kind="service", target=name, score=round(weights.static, 4), reasons=[reason]
        )
    return sorted(items.values(), key=lambda i: -i.score)


def _tests(
    history: History, changed: list[str], ranked: list[Scored], weights: Weights
) -> list[ImpactItem]:
    out: dict[str, ImpactItem] = {}
    for s in ranked:
        if is_test(s.path):
            out[s.path] = ImpactItem(
                target_kind="test", target=s.path, score=s.score, reasons=s.reasons[:2]
            )
    changed_components = {history.component(c) for c in changed}
    for f in history.active_files():
        if f in out or f in changed or not is_test(f):
            continue
        kind, name = history.component(f)
        if (kind, name) in changed_components:
            out[f] = ImpactItem(
                target_kind="test",
                target=f,
                score=round(weights.same_component, 4),
                reasons=[
                    Reason(text=f"test in changed {kind.lower()} {name}", provenance="heuristic")
                ],
            )
    for c in changed:
        if is_test(c):
            out[c] = ImpactItem(
                target_kind="test",
                target=c,
                score=1.0,
                reasons=[
                    Reason(text="this test file is part of the change", provenance="explicit")
                ],
            )
    return sorted(out.values(), key=lambda i: -i.score)[:10]


def _docs(conn: sqlite3.Connection, history: History, changed: list[str]) -> list[ImpactItem]:
    keys = [f"file:{c}" for c in changed]
    keys += sorted(
        f"{kind.lower()}:{name}" for kind, name in {history.component(c) for c in changed}
    )
    out: dict[str, ImpactItem] = {}
    for m in history.graph.mentions(keys):
        ev = m.evidence
        text = (
            f"§ {ev.get('section')} mentions {ev.get('surface')} (doc last changed {_date(m.ts)})"
        )
        item = out.setdefault(
            m.title,
            ImpactItem(target_kind="doc", target=m.title, score=0.5, reasons=[], url=m.url),
        )
        item.reasons.append(Reason(text=text, provenance="heuristic", url=m.url))
        item.score = min(1.0, 0.5 + 0.1 * (len(item.reasons) - 1))
    return sorted(out.values(), key=lambda i: -i.score)[:8]


def _runbooks(conn: sqlite3.Connection, components: list[ImpactItem]) -> list[ImpactItem]:
    """Runbook sections for the affected services (changed ones first)."""
    from hindsight.brain.uploads import runbooks_for

    out: list[ImpactItem] = []
    for comp in components:
        if comp.target_kind != "service":
            continue
        for rb in runbooks_for(conn, comp.target):
            out.append(
                ImpactItem(
                    target_kind="doc",
                    target=rb.title,
                    score=comp.score,
                    url=rb.url,
                    reasons=[
                        Reason(
                            text=f"runbook for {comp.target} (updated {_date(rb.updated_ts)})",
                            provenance="heuristic",
                            url=rb.url,
                        )
                    ],
                )
            )
    return out[:8]


def _people(conn: sqlite3.Connection, history: History, changed: list[str]) -> list[ImpactItem]:
    marks = ",".join("?" * len(changed))
    rows = conn.execute(
        f"SELECT a.title AS name, COUNT(DISTINCT c.id) AS n, MAX(c.ts) AS last_ts FROM changes ch"
        f" JOIN commits c ON c.id = ch.commit_id AND c.ok = 1 AND c.ts < ?"
        f" JOIN nodes a ON a.key = c.author_key WHERE ch.lineage IN ({marks})"
        f" GROUP BY c.author_key ORDER BY n DESC, last_ts DESC LIMIT 5",
        [history.t, *changed],
    ).fetchall()
    total = sum(int(r["n"]) for r in rows) or 1
    return [
        ImpactItem(
            target_kind="person",
            target=r["name"],
            score=round(int(r["n"]) / total, 4),
            reasons=[
                Reason(
                    text=f"{r['n']} commits to the changed files (last {_date(r['last_ts'])})",
                    provenance="explicit",
                )
            ],
        )
        for r in rows
    ]


def resolve_commit(conn: sqlite3.Connection, ref: str) -> tuple[str, int, list[str]] | None:
    row = conn.execute(
        "SELECT id, sha, ts FROM commits WHERE sha LIKE ? ORDER BY ts DESC LIMIT 1",
        (ref.strip().lower() + "%",),
    ).fetchone()
    if not row:
        return None
    files = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT lineage FROM changes WHERE commit_id = ? AND generated = 0",
            (row["id"],),
        )
    ]
    return row["sha"], int(row["ts"]), files


def impact_report(
    conn: sqlite3.Connection,
    files: list[str] | None = None,
    commit: str | None = None,
    weights: Weights | None = None,
    graph: GraphReader | None = None,
) -> ImpactReport:
    weights = weights or Weights()
    repo = get_meta(conn, "repo", {}) or {}
    web = repo.get("web", "")
    notes: list[str] = []
    as_of: int | None = None
    sha: str | None = None
    if commit:
        resolved = resolve_commit(conn, commit)
        if resolved is None:
            return ImpactReport(
                changed_files=[],
                commit=commit,
                components=[],
                files=[],
                tests=[],
                docs=[],
                people=[],
                weights=asdict(weights),
                notes=[f"commit {commit} not found"],
            )
        sha, as_of, changed = resolved
        notes.append(f"statistics use only history before {sha[:7]} ({_date(as_of)})")
    else:
        lineage = dict(conn.execute("SELECT path, lineage FROM changes").fetchall())
        changed = []
        for f in files or []:
            path = f.strip().lstrip("./")
            changed.append(lineage.get(path, path))
    history = History(conn, as_of, graph)
    for c in changed:
        if history.count(c) == 0:
            notes.append(f"{c} has no earlier history; only structural signals apply")
    now_ts = as_of or int(repo.get("head_ts") or 0) or int(datetime.now(UTC).timestamp())
    ranked = rank_files(history, changed, weights, now_ts, web) if changed else []
    files_out = [
        ImpactItem(
            target_kind="file",
            target=s.path,
            score=s.score,
            reasons=s.reasons,
            url=f"{web}/blob/HEAD/{s.path}" if web else "",
        )
        for s in ranked
        if not is_test(s.path)
    ][:15]
    components = _components(history, changed, ranked, weights, web) if changed else []
    return ImpactReport(
        changed_files=changed,
        commit=sha,
        as_of_ts=as_of,
        components=components,
        files=files_out,
        tests=_tests(history, changed, ranked, weights) if changed else [],
        docs=_docs(conn, history, changed) if changed else [],
        people=_people(conn, history, changed) if changed else [],
        runbooks=_runbooks(conn, components),
        weights=asdict(weights),
        notes=notes,
    )
