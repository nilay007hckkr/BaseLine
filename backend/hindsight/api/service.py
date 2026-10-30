"""Functions shared by the API and the CLI."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hindsight.brain.ask import ask
from hindsight.brain.ingest import parse_github
from hindsight.core.config import ROOT, Settings
from hindsight.core.db import connect, get_meta
from hindsight.core.graphstore import (
    GraphReader,
    graph_counts,
    graph_reader,
    namespace,
    neo4j_driver,
)
from hindsight.core.llm import Chain
from hindsight.core.schemas import Answer, CommitSummary, GraphStatus, ScoreCard

RESULTS_DIR = ROOT / "eval" / "results"


def primary_web(settings: Settings) -> str:
    """Web URL of the main target repository (TARGET_REPO_URL)."""
    owner, name = parse_github(settings.repo_url)
    return f"https://github.com/{owner}/{name}"


def repo_dir(conn: sqlite3.Connection) -> Path | None:
    repo = get_meta(conn, "repo") or {}
    path = repo.get("path")
    return Path(path) if path and Path(path).exists() else None


def ask_question(
    conn: sqlite3.Connection,
    settings: Settings,
    question: str,
    file: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    graph: GraphReader | None = None,
) -> Answer:
    chain = Chain.from_settings(conn, settings)
    return ask(
        conn,
        question,
        repo=repo_dir(conn),
        file=file,
        line_start=line_start,
        line_end=line_end,
        chain=chain,
        uploads_to_external=settings.docs_allow_external,
        graph=graph,
    )


def graph_status(conn: sqlite3.Connection, settings: Settings) -> GraphStatus:
    synced = get_meta(conn, "graph_sync") or {}
    counts = graph_counts(conn)
    ns = namespace(conn)
    in_sync = synced.get("ns") == ns and all(synced.get(k) == v for k, v in counts.items())
    reachable = settings.graph_backend != "sqlite" and neo4j_driver(settings) is not None
    try:
        backend = graph_reader(conn, settings).backend
    except RuntimeError:
        backend = "unavailable"
    return GraphStatus(
        backend=backend,
        configured=settings.graph_backend,
        neo4j_reachable=reachable,
        namespace=ns,
        synced_at=synced.get("synced_at"),
        in_sync=in_sync,
        nodes=counts["nodes"],
        edges=counts["edges"],
        browser_url="http://localhost:7474" if reachable else "",
    )


def resync_graph(settings: Settings) -> dict[str, object] | None:
    """Copy the graph into Neo4j again if this repository was synced before and Neo4j is up."""
    from hindsight.core.graphsync import sync

    if settings.graph_backend == "sqlite":
        return None
    conn = connect(settings.db_path)
    try:
        synced = get_meta(conn, "graph_sync") or {}
        driver = neo4j_driver(settings)
        if driver is None or synced.get("ns") != namespace(conn):
            return None
        return sync(conn, driver)
    finally:
        conn.close()


def recent_commits(
    conn: sqlite3.Connection, limit: int = 50, include_bots: bool = False
) -> list[CommitSummary]:
    web = (get_meta(conn, "repo") or {}).get("web", "")
    rows = conn.execute(
        "SELECT c.sha, c.ts, c.n_files, c.is_bot, n.title, json_extract(n.attrs, '$.author') AS author,"
        " (SELECT p.key FROM edges e JOIN nodes p ON p.id = e.dst WHERE e.src = c.id AND e.kind = 'PART_OF'"
        "  LIMIT 1) AS pr FROM commits c JOIN nodes n ON n.id = c.id"
        " WHERE (? OR c.is_bot = 0) AND c.is_merge = 0 ORDER BY c.ts DESC LIMIT ?",
        (int(include_bots), limit),
    ).fetchall()
    return [
        CommitSummary(
            sha=r["sha"],
            title=r["title"],
            ts=r["ts"],
            author=r["author"] or "",
            n_files=r["n_files"],
            is_bot=bool(r["is_bot"]),
            url=f"{web}/commit/{r['sha']}",
            pr=r["pr"],
        )
        for r in rows
    ]


MetricTable = dict[str, dict[str, dict[str, float]]]


def _cards(
    methods: MetricTable,
    labels: dict[str, str],
    *,
    suite: str,
    synthetic: bool,
    note: str,
    n: int,
    command: str,
    result_file: Path,
) -> list[ScoreCard]:
    """One card per metric: Hindsight's value next to the best baseline for that metric."""
    ours = methods["hindsight"]
    baselines = {k: v for k, v in methods.items() if k != "hindsight"}
    shown = (
        str(result_file.relative_to(ROOT)) if result_file.is_relative_to(ROOT) else str(result_file)
    )
    cards: list[ScoreCard] = []
    for metric, value in ours.items():
        best_name, best = max(baselines.items(), key=lambda kv: kv[1][metric]["value"])
        cards.append(
            ScoreCard(
                suite=suite,
                synthetic=synthetic,
                note=note,
                metric=labels.get(metric, metric),
                method="hindsight",
                value=value["value"],
                ci_low=value["ci_low"],
                ci_high=value["ci_high"],
                baseline=best_name,
                baseline_value=best[metric]["value"],
                n=n,
                command=command,
                result_file=shown,
            )
        )
    return cards


def latest_impact_by_repo(results_dir: Path = RESULTS_DIR) -> dict[str, Path]:
    """The newest impact result file per repository web URL (file names sort by time)."""
    latest: dict[str, Path] = {}
    for path in sorted(results_dir.glob("impact-*.json")):
        web = (json.loads(path.read_text()).get("repo") or {}).get("web", "")
        latest[web] = path
    return latest


def scorecard(results_dir: Path = RESULTS_DIR, primary: str | None = None) -> list[ScoreCard]:
    """Cards for the main repository (`primary` web URL; the newest result when None), then one
    'impact-other' group per other public repository evaluated with the same protocol."""
    cards: list[ScoreCard] = []
    impact = latest_impact_by_repo(results_dir)
    if primary is None and impact:
        primary = max(impact.items(), key=lambda kv: kv[1].name)[0]
    for web, path in sorted(impact.items(), key=lambda kv: (kv[0] != primary, kv[0])):
        data = json.loads(path.read_text())
        cards += _cards(
            data["methods"],
            {},
            suite="impact" if web == primary else "impact-other",
            synthetic=False,
            note=web,
            n=data["n_queries"],
            command=data["command"],
            result_file=path,
        )
    rca = sorted(results_dir.glob("rca-*.json"))
    if rca:
        data = json.loads(rca[-1].read_text())
        ttd = data["ttd_s"]
        note = (
            f"{data['detected']} of {data['n_runs']} injected faults detected · median time to"
            f" detect {ttd['median']} s · {data['false_alerts_per_hour']} false alerts per hour of"
            " healthy traffic"
        )
        cards += _cards(
            data["culprit"],
            {"top1": "Culprit top-1", "top3": "Culprit top-3"},
            suite="rca",
            synthetic=True,
            note=note,
            n=data["n_runs"],
            command=data["command"],
            result_file=rca[-1],
        )
        cards += _cards(
            data["correlation"],
            {"hit1": "Change Hit@1", "hit3": "Change Hit@3"},
            suite="rca",
            synthetic=True,
            note=note,
            n=data["detected"],
            command=data["command"],
            result_file=rca[-1],
        )
    heal = sorted(results_dir.glob("heal-*.json"))
    if heal:
        data = json.loads(heal[-1].read_text())
        methods = data["methods"]
        ours = methods["similo"]
        note = (
            f"{ours['false_heals']} false heals (target 0) · {ours['missed_defects']} defects the tests"
            f" did not exercise · median heal time"
            f" {(ours['heal_latency_ms_median'] or 0) / 1000:.1f} s, re-runs included"
        )
        table = {
            ("hindsight" if name == "similo" else f"baseline_{name}"): {
                k: m[k] for k in ("heal_rate", "cosmetic_kept_green", "correct_refusals")
            }
            for name, m in methods.items()
        }
        labels = {
            "heal_rate": "Heal rate",
            "cosmetic_kept_green": "Cosmetic changes kept green",
            "correct_refusals": "Defects refused",
        }
        for metric, n_key in (
            ("heal_rate", "n_broken"),
            ("cosmetic_kept_green", "n_cosmetic"),
            ("correct_refusals", "n_defect"),
        ):
            cards += _cards(
                {name: {metric: m[metric]} for name, m in table.items()},
                labels,
                suite="heal",
                synthetic=True,
                note=note,
                n=ours[n_key],
                command=data["command"],
                result_file=heal[-1],
            )
    return cards
