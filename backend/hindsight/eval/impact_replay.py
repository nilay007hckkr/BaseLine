"""History-replay evaluation of the impact check (EVALUATION §2).

Filtered commits are split by time (oldest 70% statistics, next 15% validation, newest 15% test).
For each test commit with 2 to 30 files, one file is revealed (seed-fixed) and every method predicts the
rest using only commits strictly before the query commit. Metrics: P@5, R@10, Hit@3, each with a
95% bootstrap CI (1,000 resamples, seed 13)."""

from __future__ import annotations

import json
import random
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hindsight.brain.impact import History, Weights, rank_files
from hindsight.core.db import get_meta
from hindsight.eval.provenance import code_sha

SEED = 13
RESAMPLES = 1000
Method = Callable[[History, str, int], list[str]]


def _hindsight(history: History, revealed: str, now: int) -> list[str]:
    return [s.path for s in rank_files(history, [revealed], Weights(), now, limit=10)]


def _cochange_only(history: History, revealed: str, now: int) -> list[str]:
    n = history.count(revealed)
    if not n:
        return []
    partners = history.partners(revealed)
    return [
        f for f, _ in sorted(partners.items(), key=lambda kv: (-kv[1][0] / n, -kv[1][1], kv[0]))
    ][:10]


def _same_directory(history: History, revealed: str, now: int) -> list[str]:
    folder = revealed.rsplit("/", 1)[0] if "/" in revealed else ""
    active = history.active_files()
    same = [
        f for f in active if f != revealed and (f.rsplit("/", 1)[0] if "/" in f else "") == folder
    ]
    return sorted(same, key=lambda f: (-active[f][1], f))[:10]


def _most_frequent(history: History, revealed: str, now: int) -> list[str]:
    active = history.active_files()
    return [f for f in sorted(active, key=lambda f: (-active[f][0], f)) if f != revealed][:10]


METHODS: dict[str, Method] = {
    "hindsight": _hindsight,
    "cochange_only": _cochange_only,
    "same_directory": _same_directory,
    "most_frequent": _most_frequent,
}


def queries(conn: sqlite3.Connection, test_share: float = 0.15) -> list[tuple[str, int, list[str]]]:
    rows = conn.execute("SELECT id, sha, ts FROM commits WHERE ok = 1 ORDER BY ts, id").fetchall()
    start = int(len(rows) * (1 - test_share))
    out = []
    for row in rows[start:]:
        files = sorted(
            {
                r[0]
                for r in conn.execute(
                    "SELECT lineage FROM changes WHERE commit_id = ? AND generated = 0",
                    (row["id"],),
                )
            }
        )
        if 2 <= len(files) <= 30:
            out.append((row["sha"], int(row["ts"]), files))
    return out


def _bootstrap(values: list[float]) -> tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    rng = random.Random(SEED)
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(RESAMPLES))
    return sum(values) / n, means[int(0.025 * RESAMPLES)], means[int(0.975 * RESAMPLES) - 1]


def run(
    conn: sqlite3.Connection,
    out_dir: Path | None = None,
    test_share: float = 0.15,
    command: str = "make eval",
) -> dict[str, Any]:
    qs = queries(conn, test_share)
    rng = random.Random(SEED)
    per_method: dict[str, dict[str, list[float]]] = {
        m: {"P@5": [], "R@10": [], "Hit@3": []} for m in METHODS
    }
    rows: list[dict[str, Any]] = []
    for sha, ts, files in qs:
        revealed = rng.choice(files)
        truth = set(files) - {revealed}
        history = History(conn, as_of=ts)
        row: dict[str, Any] = {"sha": sha[:12], "revealed": revealed, "n_truth": len(truth)}
        for name, method in METHODS.items():
            ranked = method(history, revealed, ts)
            per_method[name]["P@5"].append(len(set(ranked[:5]) & truth) / 5)
            per_method[name]["R@10"].append(len(set(ranked[:10]) & truth) / len(truth))
            per_method[name]["Hit@3"].append(1.0 if set(ranked[:3]) & truth else 0.0)
            row[name] = {m: round(per_method[name][m][-1], 4) for m in ("P@5", "R@10", "Hit@3")}
        rows.append(row)
    # the same held-out changes for both methods: a paired difference is the fair comparison
    paired: dict[str, dict[str, float]] = {}
    for metric in ("P@5", "R@10", "Hit@3"):
        diffs = [
            a - b
            for a, b in zip(
                per_method["hindsight"][metric], per_method["cochange_only"][metric], strict=True
            )
        ]
        mean, lo, hi = _bootstrap(diffs)
        paired[metric] = {"value": round(mean, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4)}
    methods: dict[str, dict[str, dict[str, float]]] = {}
    for name, metrics in per_method.items():
        methods[name] = {}
        for metric, values in metrics.items():
            mean, lo, hi = _bootstrap(values)
            methods[name][metric] = {
                "value": round(mean, 4),
                "ci_low": round(lo, 4),
                "ci_high": round(hi, 4),
            }
    stats = get_meta(conn, "stats", {}) or {}
    result = {
        "name": "impact_replay",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": command,
        "code_sha": code_sha(),
        "repo": get_meta(conn, "repo", {}),
        "config": {
            "split": {"statistics": 0.70, "validation": 0.15, "test": test_share},
            "seed": SEED,
            "bootstrap_resamples": RESAMPLES,
            "weights": Weights().__dict__,
            "weights_tuned": False,
            "query_files": "2 to 30 non-generated files",
            "filters": "bot, sweep (>30 files), merge commits and generated files excluded",
        },
        "bot_share": stats.get("bot_share"),
        "n_queries": len(qs),
        "methods": methods,
        "paired_hindsight_minus_cochange": paired,
        "rows": rows,
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"impact-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
        path.write_text(json.dumps(result, indent=2))
        result["result_file"] = str(path)
    return result
