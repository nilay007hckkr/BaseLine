"""Synthetic faults on the local Online Boutique cluster, injected and reverted with kubectl only
(the images are upstream releases, so faults are operational, not code changes). Every injection
writes its ground truth to eval/faults/; all of it is labelled synthetic.

With `deploys=True`, three synthetic deploys are recorded a few minutes before the fault: one real
historical commit that touches the culprit service (the "guilty" one) and two that touch other
services, in seeded random order. The evaluation measures where the guilty commit ranks."""

from __future__ import annotations

import json
import random
import sqlite3
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hindsight.correlate.deploys import record_deploy
from hindsight.logsense.collector import kubectl


@dataclass(frozen=True)
class Fault:
    culprit: str
    inject: list[list[str]]
    revert: list[list[str]]
    description: str


def _down(service: str) -> Fault:
    return Fault(
        service,
        [["scale", f"deployment/{service}", "--replicas=0"]],
        [["scale", f"deployment/{service}", "--replicas=1"]],
        f"{service} scaled to zero (down)",
    )


SCENARIOS: dict[str, Fault] = {
    "payment-down": _down("paymentservice"),
    "productcatalog-down": _down("productcatalogservice"),
    "currency-down": _down("currencyservice"),
    "shipping-down": _down("shippingservice"),
    "cart-down": _down("cartservice"),
    "checkout-down": _down("checkoutservice"),
    "recommendation-down": _down("recommendationservice"),
    "ad-down": _down("adservice"),
    "currency-crash": Fault(
        "currencyservice",
        [["set", "env", "deployment/currencyservice", "PORT=not-a-port"]],
        [["rollout", "undo", "deployment/currencyservice"]],
        "currencyservice crash-loops on a bad PORT value",
    ),
    "checkout-bad-config": Fault(
        "checkoutservice",
        [["set", "env", "deployment/checkoutservice", "PAYMENT_SERVICE_ADDR=nowhere:50051"]],
        [["rollout", "undo", "deployment/checkoutservice"]],
        "checkoutservice points at a payment address that does not exist (hard case)",
    ),
}

Runner = Callable[[list[str]], None]


def kubectl_runner(context: str) -> Runner:
    def run(args: list[str]) -> None:
        subprocess.run(kubectl(context, *args), check=True, capture_output=True, text=True)

    return run


def _commits_touching(conn: sqlite3.Connection, service: str, other: bool, limit: int) -> list[str]:
    """Recent human commits that change the service (other=False), or that change some other service
    and not this one (other=True)."""
    touches = (
        "EXISTS (SELECT 1 FROM changes ch WHERE ch.commit_id = c.id AND ch.generated = 0"
        " AND ch.lineage LIKE ?)"
    )
    condition = f"NOT {touches} AND {touches}" if other else touches
    params = (f"src/{service}/%", "src/%") if other else (f"src/{service}/%",)
    rows = conn.execute(
        f"SELECT c.sha FROM commits c WHERE c.ok = 1 AND {condition} ORDER BY c.ts DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [r[0] for r in rows]


def synthetic_deploys(
    conn: sqlite3.Connection, culprit: str, injected_at: int, seed: int
) -> dict[str, Any]:
    guilty = _commits_touching(conn, culprit, other=False, limit=1)
    decoys = _commits_touching(conn, culprit, other=True, limit=2)
    if not guilty:
        return {}
    shas = guilty + decoys
    rng = random.Random(seed)
    offsets = rng.sample(range(60, 900, 30), len(shas))
    for sha, offset in zip(shas, offsets, strict=True):
        record_deploy(
            conn,
            sha,
            ts=injected_at - offset,
            synthetic=True,
            note="synthetic deploy recorded by the fault campaign",
        )
    return {
        "guilty": guilty[0],
        "decoys": decoys,
        "deploy_offsets_s": dict(zip(shas, offsets, strict=True)),
    }


def inject(
    scenario: str,
    run: Runner,
    faults_dir: Path,
    conn: sqlite3.Connection | None = None,
    deploys: bool = False,
    seed: int = 13,
) -> dict[str, Any]:
    fault = SCENARIOS[scenario]
    injected_at = int(time.time())
    truth: dict[str, Any] = {
        "scenario": scenario,
        "culprit": fault.culprit,
        "description": fault.description,
        "synthetic": True,
        "injected_at": injected_at,
        "reverted_at": None,
    }
    if deploys and conn is not None:
        truth["deploys"] = synthetic_deploys(conn, fault.culprit, injected_at, seed)
    for args in fault.inject:
        run(args)
    faults_dir.mkdir(parents=True, exist_ok=True)
    path = (
        faults_dir / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(injected_at))}-{scenario}.json"
    )
    path.write_text(json.dumps(truth, indent=2))
    truth["file"] = str(path)
    return truth


def revert(scenario: str, run: Runner, faults_dir: Path) -> dict[str, Any] | None:
    fault = SCENARIOS[scenario]
    for args in fault.revert:
        run(args)
    pending = sorted(faults_dir.glob(f"*-{scenario}.json"))
    for path in reversed(pending):
        truth = json.loads(path.read_text())
        if truth.get("reverted_at") is None:
            truth["reverted_at"] = int(time.time())
            path.write_text(json.dumps(truth, indent=2))
            return truth
    return None
