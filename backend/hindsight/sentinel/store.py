"""Tests, runs and repairs in SQLite, and the tests' place in the graph.

Generating a test from a ticket also grounds it in the target repository:
- the ticket's source (issue, PR or commit) must be in the graph → Test -DERIVED_FROM-> source (explicit);
- each service the ticket names → Test -COVERS-> Service (explicit);
- each quoted UI text is looked up in the repository (`git grep`); where found, the commit that
  introduced it (`git log -S`) is recorded → Test -EXERCISES-> File (static).
Texts that the repository does not contain literally (e.g. a price computed at runtime) are reported
as not grounded; they do not reject the ticket."""

from __future__ import annotations

import difflib
import json
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

from hindsight.core.db import add_edge, node_id, upsert_node
from hindsight.sentinel.spec import Spec, Ticket, TicketError, generate, load_ticket, render

SEARCH_PATHS = ("src/frontend/templates", "src")


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def ground_text(repo: Path, text: str) -> dict[str, Any]:
    for where in SEARCH_PATHS:
        hits = _git(repo, "grep", "-n", "-w", "-F", "-e", text, "--", where).splitlines()
        hits = [h for h in hits if not h.split(":", 1)[0].endswith((".json", ".lock"))] or hits
        if hits:
            path, line, _ = hits[0].split(":", 2)
            origin = _git(repo, "log", "-S", text, "--reverse", "--format=%H", "--", path).split()
            return {
                "text": text,
                "found": True,
                "file": path,
                "line": int(line),
                "origin_commit": origin[0] if origin else None,
            }
    return {"text": text, "found": False}


def _quoted(spec: Spec) -> list[tuple[int, str]]:
    out = []
    for i, step in enumerate(spec.steps):
        if step.op == "click" and step.target:
            out.append((i, step.target.name))
        elif step.op.startswith("expect"):
            out.append((i, step.value))
    return out


def register(
    conn: sqlite3.Connection, ticket_path: Path, repo: Path | None, web: str = ""
) -> dict[str, Any]:
    """Generate (or regenerate) the test for one ticket and link it into the graph."""
    now = int(time.time())
    try:
        ticket: Ticket = load_ticket(ticket_path)
        spec = generate(ticket)
    except (TicketError, ValueError) as exc:
        test_id = ticket_path.stem.upper()
        _save_test(conn, test_id, ticket_path.stem, "rejected", str(exc), None, [], now)
        return {"id": test_id, "status": "rejected", "reason": str(exc)}
    source_id = node_id(conn, spec.source)
    if source_id is None:
        reason = f"source {spec.source} is not in the graph (ingest the repository first)"
        _save_test(conn, spec.id, spec.title, "rejected", reason, spec, [], now)
        return {"id": spec.id, "status": "rejected", "reason": reason}
    grounding = [
        {"step": i, **ground_text(repo, text)}
        if repo
        else {"step": i, "text": text, "found": False}
        for i, text in _quoted(spec)
    ]
    old = conn.execute("SELECT spec, status FROM qa_tests WHERE id = ?", (spec.id,)).fetchone()
    if old and old["status"] == "active":
        # keep fingerprints and approved repairs: regeneration must not undo them silently
        prev = Spec.model_validate_json(old["spec"])
        if [s.line for s in prev.steps] == [s.line for s in spec.steps]:
            spec = prev
    _save_test(
        conn,
        spec.id,
        spec.title,
        "unvalidated" if not old else old["status"],
        "",
        spec,
        grounding,
        now,
    )
    tid = upsert_node(
        conn,
        "Test",
        f"test:{spec.id}",
        title=spec.title,
        url=f"{web}/blob/HEAD/target/tickets/{ticket_path.name}" if web else "",
        ts=now,
        attrs={"ticket": ticket_path.name},
    )
    ticket_ref = {"ticket": f"target/tickets/{ticket_path.name}"}
    add_edge(conn, tid, source_id, "DERIVED_FROM", "explicit", {**ticket_ref, "field": "source"})
    for service in spec.services:
        sid = node_id(conn, f"service:{service}")
        if sid is not None:
            add_edge(conn, tid, sid, "COVERS", "explicit", {**ticket_ref, "field": "services"})
    for g in grounding:
        if g.get("found") and (fid := node_id(conn, f"file:{g['file']}")) is not None:
            evidence = {"text": g["text"], "file": g["file"], "line": g["line"]}
            if g.get("origin_commit"):
                evidence["introduced_by"] = g["origin_commit"]
            add_edge(conn, tid, fid, "EXERCISES", "static", evidence)
    return {
        "id": spec.id,
        "status": "registered",
        "grounded": sum(bool(g["found"]) for g in grounding),
        "texts": len(grounding),
    }


def _save_test(
    conn: sqlite3.Connection,
    test_id: str,
    title: str,
    status: str,
    reason: str,
    spec: Spec | None,
    grounding: list[dict[str, Any]],
    now: int,
) -> None:
    conn.execute(
        "INSERT INTO qa_tests(id, title, status, reason, spec, grounding, version, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title = excluded.title,"
        " status = excluded.status, reason = excluded.reason, spec = excluded.spec,"
        " grounding = excluded.grounding, version = excluded.version, updated_at = excluded.updated_at",
        (
            test_id,
            title,
            status,
            reason,
            spec.model_dump_json() if spec else "{}",
            json.dumps(grounding),
            spec.version if spec else 1,
            now,
            now,
        ),
    )


def get_spec(conn: sqlite3.Connection, test_id: str) -> Spec | None:
    row = conn.execute("SELECT spec, status FROM qa_tests WHERE id = ?", (test_id,)).fetchone()
    if row is None or row["status"] == "rejected":
        return None
    return Spec.model_validate_json(row["spec"])


def save_spec(conn: sqlite3.Connection, spec: Spec, status: str | None = None) -> None:
    conn.execute(
        "UPDATE qa_tests SET spec = ?, version = ?, updated_at = ?"
        + (", status = ?" if status else "")
        + " WHERE id = ?",
        (
            spec.model_dump_json(),
            spec.version,
            int(time.time()),
            *((status,) if status else ()),
            spec.id,
        ),
    )


def save_run(conn: sqlite3.Connection, spec: Spec, result: dict[str, Any]) -> int:
    row = conn.execute(
        "INSERT INTO qa_runs(test_id, version, outcome, reason, detail, started_at, duration_ms, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (
            spec.id,
            spec.version,
            result["outcome"],
            result.get("reason", ""),
            json.dumps(
                {k: v for k, v in result.items() if k not in ("outcome", "reason", "repairs")}
            ),
            int(result["started_at"]),
            int(result["duration_ms"]),
            int(time.time()),
        ),
    ).fetchone()
    run_id = int(row[0])
    for repair in result.get("repairs", []):
        conn.execute(
            "INSERT INTO qa_repairs(test_id, run_id, step, status, data, created_at)"
            " VALUES (?, ?, ?, 'proposed', ?, ?)",
            (spec.id, run_id, repair["step"], json.dumps(repair), int(time.time())),
        )
    return run_id


def patch_for(spec: Spec, step: int, new_locator: dict[str, str]) -> str:
    """Unified diff of the rendered test when one click locator is replaced."""
    from hindsight.sentinel.spec import Locator

    after = spec.model_copy(deep=True)
    after.steps[step].target = Locator(**new_locator)
    return "".join(
        difflib.unified_diff(
            render(spec).splitlines(keepends=True),
            render(after).splitlines(keepends=True),
            fromfile=f"{spec.id}.py (v{spec.version})",
            tofile=f"{spec.id}.py (v{spec.version + 1})",
        )
    )


def decide_repair(conn: sqlite3.Connection, repair_id: int, decision: str) -> dict[str, Any]:
    """approve: apply the new locator and fingerprint (new spec version); reject: keep the test as
    is; revert: put an approved repair's old locator back. Assertions are never touched."""
    from hindsight.sentinel.spec import Locator

    row = conn.execute("SELECT * FROM qa_repairs WHERE id = ?", (repair_id,)).fetchone()
    if row is None:
        raise KeyError(repair_id)
    data = json.loads(row["data"])
    spec = get_spec(conn, row["test_id"])
    if spec is None:
        raise KeyError(row["test_id"])
    allowed = {"approve": ("proposed",), "reject": ("proposed",), "revert": ("approved",)}
    if row["status"] not in allowed[decision]:
        raise ValueError(f"repair {repair_id} is {row['status']}; cannot {decision} it")
    step = spec.steps[row["step"]]
    if step.op != "click":
        raise ValueError("only click locators are ever repaired")
    if decision in ("approve", "revert"):
        use_new = decision == "approve"
        step.target = Locator(**(data["new_locator"] if use_new else data["old_locator"]))
        step.fingerprint = data["new_fingerprint"] if use_new else data["old_fingerprint"]
        spec.version += 1
        save_spec(conn, spec)
    status = {"approve": "approved", "reject": "rejected", "revert": "reverted"}[decision]
    conn.execute(
        "UPDATE qa_repairs SET status = ?, decided_at = ? WHERE id = ?",
        (status, int(time.time()), repair_id),
    )
    return {"id": repair_id, "status": status, "test": spec.id, "version": spec.version}
