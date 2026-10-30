"""Healing evaluation on the mutation harness (EVALUATION §4). SYNTHETIC mutations, real shop.

For every mutation and every test it affects, the test runs three ways: with the Similo-style
healer, with the naive baseline (first element with the same tag and text), and with no healing.

Per run, the result is one of:
- cosmetic: passed (no heal needed) · correct heal (landed on the marked element) · false heal
  (healed onto another element) · missed (failed or handed to a person);
- defect: correct refusal (failed or handed to a person) · false heal (healed and passed) ·
  missed defect (passed without healing: the test does not exercise the defect).

Metrics: heal rate on the runs whose locator really broke (the no-heal run failed), false heals
(target 0), correct refusals, and median heal latency. 95% bootstrap CIs, seed 13."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from hindsight.eval.rca import _ci as bootstrap_ci
from hindsight.sentinel.mutations import Mutation, catalogue
from hindsight.sentinel.runner import Options, run
from hindsight.sentinel.spec import Spec

METHODS = ("similo", "naive", "none")


def classify(mutation: Mutation, result: dict[str, Any]) -> str:
    outcome = result["outcome"]
    if mutation.kind == "cosmetic":
        if outcome == "passed":
            return "passed"
        if outcome == "healed":
            truth = all(r.get("chosen_is_truth") for r in result["repairs"])
            return "correct_heal" if truth else "false_heal"
        return "missed"
    if outcome in ("failed", "needs_human"):
        return "correct_refusal"
    if outcome == "healed":
        return "false_heal"
    return "missed_defect"  # passed, or flaky


def validate(
    browser: Any, spec: Spec, base_url: str, runs: int = 3
) -> tuple[bool, Spec, list[str]]:
    """3/3 clean runs (DESIGN §5.2); fingerprints come from the last passing run."""
    reasons = []
    last: dict[str, Any] | None = None
    for _ in range(runs):
        result = run(browser, spec, Options(base_url, heal="none", capture=True))
        if result["outcome"] != "passed":
            reasons.append(result["reason"])
        last = result
    if reasons or last is None:
        return False, spec, reasons
    validated = spec.model_copy(deep=True)
    for index, fp in last["fingerprints"].items():
        validated.steps[int(index)].fingerprint = fp
    return True, validated, []


def evaluate(specs: list[Spec], base_url: str, out_dir: Path | None = None) -> dict[str, Any]:
    mutations = catalogue()
    rows: list[dict[str, Any]] = []
    started = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            ready: list[Spec] = []
            invalid: dict[str, list[str]] = {}
            for spec in specs:
                ok, validated, reasons = validate(browser, spec, base_url)
                if ok:
                    ready.append(validated)
                else:
                    invalid[spec.id] = reasons
            for m in mutations:
                for spec in (s for s in ready if m.affects(s)):
                    for method in METHODS:
                        install, state = m.installer()
                        result = run(browser, spec, Options(base_url, heal=method, install=install))
                        rows.append(
                            {
                                "mutation": m.id,
                                "kind": m.kind,
                                "description": m.description,
                                "test": spec.id,
                                "method": method,
                                "applied": state["applied"] > 0,
                                "outcome": result["outcome"],
                                "reason": result["reason"],
                                "class": classify(m, result),
                                "heal_ms": [r["heal_ms"] for r in result["repairs"]],
                                "score": [r.get("score") for r in result["repairs"]]
                                + [r.get("score") for r in result["attempted_repairs"]],
                            }
                        )
        finally:
            browser.close()
    valid = [r for r in rows if r["applied"]]
    broken = {
        (r["mutation"], r["test"])
        for r in valid
        if r["kind"] == "cosmetic" and r["method"] == "none" and r["outcome"] != "passed"
    }
    methods: dict[str, Any] = {}
    for method in METHODS:
        mine = [r for r in valid if r["method"] == method]
        cosmetic = [r for r in mine if r["kind"] == "cosmetic"]
        defect = [r for r in mine if r["kind"] == "defect"]
        on_broken = [r for r in cosmetic if (r["mutation"], r["test"]) in broken]
        latencies = [ms for r in mine for ms in r["heal_ms"]]
        methods[method] = {
            "heal_rate": bootstrap_ci(
                [1.0 if r["class"] == "correct_heal" else 0.0 for r in on_broken]
            ),
            "cosmetic_kept_green": bootstrap_ci(
                [1.0 if r["class"] in ("passed", "correct_heal") else 0.0 for r in cosmetic]
            ),
            "correct_refusals": bootstrap_ci(
                [1.0 if r["class"] == "correct_refusal" else 0.0 for r in defect]
            ),
            "false_heals": sum(r["class"] == "false_heal" for r in mine),
            "missed_defects": sum(r["class"] == "missed_defect" for r in defect),
            "n_cosmetic": len(cosmetic),
            "n_broken": len(on_broken),
            "n_defect": len(defect),
            "heal_latency_ms_median": statistics.median(latencies) if latencies else None,
        }
    result = {
        "name": "heal_mutation_harness",
        "synthetic": True,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "command": "make eval-heal",
        "base_url": base_url,
        "config": {
            "theta": 0.75,
            "delta": 0.15,
            "reruns_before_heal": 2,
            "n_mutations": {
                "cosmetic": sum(m.kind == "cosmetic" for m in mutations),
                "defect": sum(m.kind == "defect" for m in mutations),
            },
            "tests": [s.id for s in specs],
        },
        "invalid_tests": invalid,
        "not_applied": sorted({(r["mutation"], r["test"]) for r in rows if not r["applied"]}),
        "methods": methods,
        "runs": rows,
        "seconds": round(time.time() - started),
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"heal-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
        path.write_text(json.dumps(result, indent=2))
        result["result_file"] = str(path)
    return result
