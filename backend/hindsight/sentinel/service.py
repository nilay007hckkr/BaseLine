"""SentinelQA operations shared by the CLI and the API: generate, validate, run, review repairs."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from hindsight.core.config import ROOT, Settings
from hindsight.core.db import get_meta
from hindsight.core.schemas import QaGrounding, QaRepair, QaRun, QaTest
from hindsight.sentinel.spec import Spec, render
from hindsight.sentinel.store import get_spec, register, save_run, save_spec

TICKETS_DIR = ROOT / "target" / "tickets"
INCIDENT_FRESH_S = 900  # an incident updated in the last 15 minutes and not closed counts as open


def shots_dir(settings: Settings) -> Path:
    return settings.data_dir / "sentinel" / "shots"


def generate_all(conn: sqlite3.Connection, tickets_dir: Path = TICKETS_DIR) -> list[dict[str, Any]]:
    repo = get_meta(conn, "repo") or {}
    path = Path(repo["path"]) if repo.get("path") and Path(repo["path"]).exists() else None
    return [
        register(conn, t, path, repo.get("web", "")) for t in sorted(tickets_dir.glob("*.yaml"))
    ]


def open_incident_check(conn: sqlite3.Connection) -> Any:
    def check(services: list[str]) -> str | None:
        rows = conn.execute(
            "SELECT id, data FROM incidents WHERE closed_at IS NULL AND updated_at >= ?",
            (int(time.time()) - INCIDENT_FRESH_S,),
        ).fetchall()
        for row in rows:
            hit = sorted(
                {r["service"] for r in json.loads(row["data"]).get("ranking", [])} & set(services)
            )
            if hit:
                return f"incident #{row['id']} is open on {', '.join(hit)}"
        return None

    return check


def _ids(conn: sqlite3.Connection, ids: list[str] | None, statuses: tuple[str, ...]) -> list[str]:
    marks = ",".join("?" * len(statuses))
    rows = conn.execute(f"SELECT id FROM qa_tests WHERE status IN ({marks}) ORDER BY id", statuses)
    found = [r[0] for r in rows]
    return [i for i in found if not ids or i in ids]


def validate_tests(
    conn: sqlite3.Connection, settings: Settings, ids: list[str] | None = None
) -> list[dict[str, Any]]:
    from playwright.sync_api import sync_playwright

    from hindsight.eval.heal import validate

    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for test_id in _ids(conn, ids, ("unvalidated", "active")):
                spec = get_spec(conn, test_id)
                if spec is None:
                    continue
                ok, validated, reasons = validate(browser, spec, settings.target_url)
                if ok:
                    save_spec(conn, validated, status="active")
                    conn.execute("UPDATE qa_tests SET reason = '' WHERE id = ?", (test_id,))
                else:
                    reason = "did not pass 3/3 on the clean shop: " + "; ".join(reasons)[:500]
                    conn.execute(
                        "UPDATE qa_tests SET status = 'unvalidated', reason = ? WHERE id = ?",
                        (reason, test_id),
                    )
                conn.commit()
                out.append({"id": test_id, "validated": ok, "reasons": reasons})
        finally:
            browser.close()
    return out


def run_tests(
    conn: sqlite3.Connection, settings: Settings, ids: list[str] | None = None
) -> list[dict[str, Any]]:
    from playwright.sync_api import sync_playwright

    from hindsight.sentinel.runner import Options, run

    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for test_id in _ids(conn, ids, ("active",)):
                spec = get_spec(conn, test_id)
                if spec is None:
                    continue
                result = run(
                    browser,
                    spec,
                    Options(
                        settings.target_url,
                        heal="similo",
                        shots_dir=shots_dir(settings),
                        open_incident=open_incident_check(conn),
                    ),
                )
                run_id = save_run(conn, spec, result)
                conn.commit()
                out.append(
                    {
                        "id": test_id,
                        "run": run_id,
                        "outcome": result["outcome"],
                        "reason": result["reason"],
                    }
                )
        finally:
            browser.close()
    return out


def list_tests(conn: sqlite3.Connection) -> list[QaTest]:
    out = []
    for r in conn.execute("SELECT * FROM qa_tests ORDER BY id"):
        spec = Spec.model_validate_json(r["spec"]) if r["spec"] != "{}" else None
        source = None
        if spec is not None:
            source = conn.execute(
                "SELECT kind, title, url FROM nodes WHERE key = ?", (spec.source,)
            ).fetchone()
        last = conn.execute(
            "SELECT * FROM qa_runs WHERE test_id = ? ORDER BY id DESC LIMIT 1", (r["id"],)
        ).fetchone()
        out.append(
            QaTest(
                id=r["id"],
                title=r["title"],
                status=r["status"],
                reason=r["reason"],
                version=r["version"],
                source_key=spec.source if spec else "",
                source_kind=source["kind"] if source else "",
                source_title=source["title"] if source else "",
                source_url=source["url"] if source else "",
                services=spec.services if spec else [],
                steps=[s.line.strip() for s in spec.steps] if spec else [],
                grounding=[QaGrounding(**g) for g in json.loads(r["grounding"])],
                code=render(spec) if spec else "",
                last_run=_run(last) if last else None,
            )
        )
    return out


def _run(r: sqlite3.Row) -> QaRun:
    return QaRun(
        id=r["id"],
        test_id=r["test_id"],
        version=r["version"],
        outcome=r["outcome"],
        reason=r["reason"],
        started_at=r["started_at"],
        duration_ms=r["duration_ms"],
    )


def list_repairs(conn: sqlite3.Connection) -> list[QaRepair]:
    from hindsight.sentinel.store import patch_for

    out = []
    for r in conn.execute("SELECT * FROM qa_repairs ORDER BY id DESC LIMIT 100"):
        data = json.loads(r["data"])
        spec = get_spec(conn, r["test_id"])
        out.append(
            QaRepair(
                id=r["id"],
                test_id=r["test_id"],
                run_id=r["run_id"],
                step=r["step"],
                line=data.get("line", ""),
                status=r["status"],
                old_locator=data["old_locator"],
                new_locator=data["new_locator"],
                score=data.get("score"),
                runner_up=data.get("runner_up"),
                breakdown=data.get("breakdown", {}),
                before_shot=data.get("before_shot"),
                after_shot=data.get("after_shot"),
                patch=patch_for(spec, r["step"], data["new_locator"]) if spec else "",
                created_at=r["created_at"],
            )
        )
    return out
