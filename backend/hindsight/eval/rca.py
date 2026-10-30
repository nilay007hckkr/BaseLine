"""Fault campaign on the local cluster (EVALUATION §5 and §6). Synthetic by construction.

Per run: healthy traffic (false alerts counted) → inject one scenario with synthetic deploys →
wait for the first incident → let the ranking settle → read it → revert → recover. The live
collector and engine run in a background thread the whole time, exactly as `make collect` does.

Metrics, each with a 95% bootstrap CI: top-1 / top-3 culprit accuracy, time to detect (median and
p90), false alerts per hour of healthy traffic, and the guilty commit's rank among change
candidates (Hit@1, Hit@3). Baselines: "service with the most error lines", "service with the largest
error increase", and "most recent deploy" for correlation."""

from __future__ import annotations

import json
import random
import statistics
import threading
import time
from pathlib import Path
from typing import Any

from hindsight.core.config import ROOT, Settings
from hindsight.core.db import connect
from hindsight.correlate.deploys import candidates
from hindsight.logsense.collector import Collector, remember_addresses
from hindsight.logsense.engine import Engine, service_aliases
from hindsight.logsense.faults import SCENARIOS, inject, kubectl_runner, revert

SEED = 13
RESAMPLES = 1000


def _ci(values: list[float]) -> dict[str, float]:
    if not values:
        return {"value": 0.0, "ci_low": 0.0, "ci_high": 0.0}
    rng = random.Random(SEED)
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(RESAMPLES))
    return {
        "value": round(sum(values) / n, 4),
        "ci_low": round(means[int(0.025 * RESAMPLES)], 4),
        "ci_high": round(means[int(0.975 * RESAMPLES) - 1], 4),
    }


def _errors(conn: Any, start: float, end: float) -> dict[str, int]:
    rows = conn.execute(
        "SELECT tc.service, SUM(tc.count) FROM template_counts tc JOIN templates t"
        " ON t.id = tc.template_id AND t.source = tc.source"
        " WHERE tc.source LIKE 'eval-%' AND t.severity = 'error' AND tc.win >= ? AND tc.win < ?"
        " GROUP BY tc.service",
        (int(start), int(end)),
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def _rank(scores: dict[str, float]) -> list[str]:
    return [s for s, v in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])) if v > 0]


def campaign(
    settings: Settings,
    runs: int = 10,
    healthy_s: int = 300,
    detect_timeout_s: int = 180,
    settle_s: int = 60,
    recovery_s: int = 120,
    warmup_s: int = 300,
    scenarios: list[str] | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    scenarios = scenarios or list(SCENARIOS)
    context = f"kind-{settings.kind_cluster}"
    faults_dir = ROOT / "eval" / "faults"
    stop = threading.Event()

    def collect() -> None:
        c = connect(settings.db_path)
        remember_addresses(c, context)
        engine = Engine(c, service_aliases(c), warmup=warmup_s, source="eval")
        collector = Collector(engine, context, settings.data_dir / "logs")
        collector.stop = stop
        collector.run()
        engine.flush()

    thread = threading.Thread(target=collect, daemon=True)
    thread.start()
    conn = connect(settings.db_path)
    run = kubectl_runner(context)
    results: list[dict[str, Any]] = []
    healthy_seconds = 0.0
    false_alerts = 0
    time.sleep(warmup_s + 30)  # learn normal log patterns first
    try:
        for i in range(runs):
            scenario = scenarios[i % len(scenarios)]
            healthy_start = time.time()
            time.sleep(healthy_s)
            healthy_end = time.time()
            healthy_seconds += healthy_end - healthy_start
            false_alerts += conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE source LIKE 'eval-%' AND opened_at BETWEEN ? AND ?",
                (int(healthy_start), int(healthy_end)),
            ).fetchone()[0]
            truth = inject(scenario, run, faults_dir, conn=conn, deploys=True, seed=SEED + i)
            injected = truth["injected_at"]
            incident = None
            while time.time() < injected + detect_timeout_s and incident is None:
                time.sleep(5)
                incident = conn.execute(
                    "SELECT id, opened_at FROM incidents WHERE source LIKE 'eval-%' AND opened_at >= ?"
                    " ORDER BY opened_at LIMIT 1",
                    (injected - 10,),
                ).fetchone()
            if incident is not None:
                time.sleep(settle_s)
            fault_end = time.time()
            revert(scenario, run, faults_dir)
            result: dict[str, Any] = {
                "scenario": scenario,
                "truth": truth["culprit"],
                "injected_at": injected,
            }
            healthy_err = _errors(conn, healthy_start, healthy_end)
            fault_err = _errors(conn, injected, fault_end)
            scale = (fault_end - injected) / max(1.0, healthy_end - healthy_start)
            result["baseline_most_errors"] = _rank({s: float(n) for s, n in fault_err.items()})
            result["baseline_largest_increase"] = _rank(
                {s: n - healthy_err.get(s, 0) * scale for s, n in fault_err.items()}
            )
            if incident is None:
                result.update({"detected": False, "ranking": []})
            else:
                data = json.loads(
                    conn.execute(
                        "SELECT data FROM incidents WHERE id = ?", (incident["id"],)
                    ).fetchone()[0]
                )
                ranking = [r["service"] for r in data["ranking"]]
                culprit = ranking[0] if ranking else None
                samples = " ".join(s for r in data["ranking"] for s in r["samples"])
                cands = [
                    c.commit for c in candidates(conn, incident["opened_at"], culprit, samples)
                ]
                guilty = truth.get("deploys", {}).get("guilty")
                recent = [
                    r[0]
                    for r in conn.execute(
                        "SELECT commit_sha FROM deploys WHERE ts <= ? ORDER BY ts DESC LIMIT 3",
                        (incident["opened_at"],),
                    )
                ]
                result.update(
                    {
                        "detected": True,
                        "incident": incident["id"],
                        "ttd_s": incident["opened_at"] - injected,
                        "ranking": ranking,
                        "candidates": cands[:5],
                        "guilty": guilty,
                        "guilty_rank": cands.index(guilty) + 1 if guilty in cands else None,
                        "baseline_recent_rank": recent.index(guilty) + 1
                        if guilty in recent
                        else None,
                    }
                )
            results.append(result)
            time.sleep(recovery_s)
    finally:
        stop.set()
        thread.join(timeout=15)

    def hit(rank_list: list[str], truth: str, k: int) -> float:
        return 1.0 if truth in rank_list[:k] else 0.0

    detected = [r for r in results if r["detected"]]
    ttds = sorted(r["ttd_s"] for r in detected)
    metrics = {
        "hindsight": {
            "top1": _ci([hit(r["ranking"], r["truth"], 1) for r in results]),
            "top3": _ci([hit(r["ranking"], r["truth"], 3) for r in results]),
        },
        "baseline_most_errors": {
            "top1": _ci([hit(r["baseline_most_errors"], r["truth"], 1) for r in results]),
            "top3": _ci([hit(r["baseline_most_errors"], r["truth"], 3) for r in results]),
        },
        "baseline_largest_increase": {
            "top1": _ci([hit(r["baseline_largest_increase"], r["truth"], 1) for r in results]),
            "top3": _ci([hit(r["baseline_largest_increase"], r["truth"], 3) for r in results]),
        },
    }
    correlation = {
        "hindsight": {
            "hit1": _ci([1.0 if r.get("guilty_rank") == 1 else 0.0 for r in detected]),
            "hit3": _ci([1.0 if (r.get("guilty_rank") or 99) <= 3 else 0.0 for r in detected]),
        },
        "baseline_most_recent_deploy": {
            "hit1": _ci([1.0 if r.get("baseline_recent_rank") == 1 else 0.0 for r in detected]),
            "hit3": _ci(
                [1.0 if (r.get("baseline_recent_rank") or 99) <= 3 else 0.0 for r in detected]
            ),
        },
    }
    result = {
        "name": "rca_campaign",
        "synthetic": True,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "command": f"make eval-rca RUNS={runs}",
        "config": {
            "runs": runs,
            "healthy_s": healthy_s,
            "detect_timeout_s": detect_timeout_s,
            "settle_s": settle_s,
            "recovery_s": recovery_s,
            "warmup_s": warmup_s,
            "seed": SEED,
            "scenarios": scenarios,
        },
        "n_runs": len(results),
        "detected": len(detected),
        "ttd_s": {
            "median": statistics.median(ttds) if ttds else None,
            "p90": ttds[int(0.9 * (len(ttds) - 1))] if ttds else None,
        },
        "false_alerts_per_hour": round(false_alerts / max(healthy_seconds / 3600, 1e-9), 3),
        "culprit": metrics,
        "correlation": correlation,
        "runs": results,
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"rca-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
        path.write_text(json.dumps(result, indent=2))
        result["result_file"] = str(path)
    return result
