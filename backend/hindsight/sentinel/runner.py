"""Run a test spec in a real browser (Playwright, Chromium), and heal broken click locators
(DESIGN §5.3 and §5.4).

Outcomes:
- passed       every step and assertion held;
- healed       a click locator was broken, the healer found the same control, and every original
               assertion held afterwards (a repair is proposed for a person to approve);
- failed       a genuine failure: an assertion failed, the control is disabled, the server returned
               5xx or a gRPC error showed on the page; never healed;
- needs_human  a locator broke and no candidate was clearly the same control (θ, δ), or an
               incident is open on a service the test covers;
- flaky        the step failed once but the test passed on a re-run.

The healer only ever replaces a click locator. Assertions are never modified or skipped."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Error, Locator, Page

from hindsight.sentinel.fingerprint import (
    CANDIDATES,
    CANDIDATES_JS,
    FINGERPRINT_JS,
    choose,
    naive,
    stored,
)
from hindsight.sentinel.spec import Locator as SpecLocator
from hindsight.sentinel.spec import Spec, Step

FIND_TIMEOUT_MS = 3000
RERUNS = 2  # re-runs before healing, to rule out flakiness

Install = Callable[[Page], None]  # e.g. a mutation that rewrites pages through page.route
IncidentCheck = Callable[[list[str]], str | None]  # open incident on a covered service → reason


@dataclass
class Options:
    base_url: str
    heal: str = "similo"  # similo | naive | none
    install: Install | None = None
    shots_dir: Path | None = None
    reruns: int = RERUNS
    open_incident: IncidentCheck | None = None
    capture: bool = False  # save fingerprints for click steps (validation runs)


class _Stop(Exception):
    def __init__(self, outcome: str, reason: str) -> None:
        super().__init__(reason)
        self.outcome, self.reason = outcome, reason


def _locator(page: Page, loc: SpecLocator) -> Locator:
    if loc.role:
        return page.get_by_role(loc.role, name=loc.name, exact=True)  # type: ignore[arg-type]
    return page.locator(loc.css)


def _visible(loc: Locator, timeout_ms: float) -> bool:
    try:
        loc.first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Error:
        return False


def _shot(page: Page, opts: Options, name: str) -> str | None:
    if opts.shots_dir is None:
        return None
    opts.shots_dir.mkdir(parents=True, exist_ok=True)
    path = opts.shots_dir / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except Error:
        return None
    return path.name


def _backend_error(page: Page, server_errors: list[str]) -> str | None:
    if server_errors:
        return f"server error during the test: {server_errors[-1]}"
    try:
        body = page.inner_text("body", timeout=1000)
    except Error:
        return None
    if "rpc error" in body:
        line = next((ln for ln in body.splitlines() if "rpc error" in ln), "rpc error")
        return f"gRPC error shown on the page: {line.strip()[:200]}"
    return None


def _new_locator(page: Page, fp: dict[str, Any], index: int) -> SpecLocator:
    """Prefer role + exact accessible name when it is unique; else the id; else a flagged CSS path."""
    named = fp.get("role") in ("button", "link") and fp.get("name")
    if named and page.get_by_role(fp["role"], name=fp["name"], exact=True).count() == 1:
        return SpecLocator(role=fp["role"], name=fp["name"])
    if fp.get("id") and page.locator(f"#{fp['id']}").count() == 1:
        return SpecLocator(css=f"#{fp['id']}")
    return SpecLocator(css=f"{CANDIDATES} >> nth={index}")


def run(browser: Browser, spec: Spec, opts: Options) -> dict[str, Any]:
    started = time.time()
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    server_errors: list[str] = []
    page.on(
        "response",
        lambda r: (
            server_errors.append(f"HTTP {r.status} for {r.request.method} {r.url}")
            if r.status >= 500
            else None
        ),
    )
    if opts.install:
        opts.install(page)
    repairs: list[dict[str, Any]] = []
    fingerprints: dict[int, dict[str, Any]] = {}
    steps_done = 0
    outcome, reason = "passed", ""
    try:
        for i, step in enumerate(spec.steps):
            _run_step(page, spec, i, step, opts, server_errors, repairs, fingerprints, browser)
            steps_done = i + 1
        if repairs:
            outcome = "healed"
            for r in repairs:
                r["after_shot"] = _shot(page, opts, f"{spec.id}-{int(started)}-{r['step']}-after")
    except _Stop as stop:
        outcome, reason = stop.outcome, stop.reason
        if repairs and outcome == "failed":
            reason = f"after a heal, so the heal is discarded: {reason}"
    finally:
        context.close()
    return {
        "outcome": outcome,
        "reason": reason,
        "repairs": repairs if outcome == "healed" else [],
        "attempted_repairs": repairs if outcome != "healed" else [],
        "fingerprints": {str(k): v for k, v in fingerprints.items()},
        "steps_done": steps_done,
        "server_errors": server_errors[-5:],
        "started_at": started,
        "duration_ms": int((time.time() - started) * 1000),
        "heal_mode": opts.heal,
    }


def _run_step(
    page: Page,
    spec: Spec,
    i: int,
    step: Step,
    opts: Options,
    server_errors: list[str],
    repairs: list[dict[str, Any]],
    fingerprints: dict[int, dict[str, Any]],
    browser: Browser,
) -> None:
    if step.op == "goto":
        page.goto(opts.base_url.rstrip("/") + step.path, wait_until="domcontentloaded")
        return
    if step.op in ("expect_heading", "expect_text"):
        loc = (
            _locator(page, step.target)
            if step.op == "expect_heading" and step.target
            else page.get_by_text(step.value)
        )
        if not _visible(loc, step.within_s * 1000):
            backend = _backend_error(page, server_errors)
            raise _Stop(
                "failed",
                f"assertion failed: {step.line.strip()}" + (f" ({backend})" if backend else ""),
            )
        return
    assert step.op == "click" and step.target is not None
    loc = _locator(page, step.target)
    if _visible(loc, FIND_TIMEOUT_MS):
        element = loc.first
        if opts.capture:
            fingerprints[i] = stored(element.evaluate(FINGERPRINT_JS))
        if element.is_disabled():
            raise _Stop("failed", f"the control for '{step.line.strip()}' is disabled")
        element.click()
        page.wait_for_load_state("domcontentloaded")
        return
    _heal(page, spec, i, step, opts, server_errors, repairs, browser)


def _heal(
    page: Page,
    spec: Spec,
    i: int,
    step: Step,
    opts: Options,
    server_errors: list[str],
    repairs: list[dict[str, Any]],
    browser: Browser,
) -> None:
    assert step.target is not None
    broken = f"locator not found: {step.target.describe()} ({step.line.strip()})"
    if opts.heal == "none" or step.fingerprint is None:
        raise _Stop("failed" if opts.heal == "none" else "needs_human", broken)
    t0 = time.time()
    # refusal rules first: a real defect must never be healed away
    backend = _backend_error(page, server_errors)
    if backend:
        raise _Stop("failed", f"{broken}; not healed: {backend}")
    if opts.open_incident and (incident := opts.open_incident(spec.services)):
        raise _Stop("needs_human", f"{broken}; not healed: {incident}")
    before = _shot(page, opts, f"{spec.id}-{int(t0)}-{i}-before")
    # rule out flakiness: the unchanged test, fresh browser context, no healing
    for _ in range(opts.reruns):
        again = run(browser, spec, Options(opts.base_url, "none", opts.install, None, 0))
        if again["outcome"] == "passed":
            raise _Stop("flaky", f"{broken}; passed on a re-run")
    candidates: list[dict[str, Any]] = page.evaluate(CANDIDATES_JS, CANDIDATES)
    if opts.heal == "naive":
        index = naive(step.fingerprint, candidates)
        if index is None:
            raise _Stop("failed", f"{broken}; naive: no element with the same tag and text")
        choice_info: dict[str, Any] = {"method": "naive"}
    else:
        choice = choose(step.fingerprint, candidates)
        if choice.index is None:
            raise _Stop("needs_human", f"{broken}; no confident match: {choice.reason}")
        index = choice.index
        choice_info = {
            "method": "similo",
            "score": choice.score,
            "runner_up": choice.runner_up,
            "breakdown": choice.breakdown,
            "top": [
                {"name": candidates[j].get("name", ""), "tag": candidates[j].get("tag"), "score": s}
                for j, s in choice.ranked
            ],
        }
    picked = candidates[index]
    if picked.get("disabled"):
        raise _Stop("failed", f"{broken}; the matching control is disabled")
    new = _new_locator(page, picked, index)
    repairs.append(
        {
            "step": i,
            "line": step.line,
            "old_locator": step.target.model_dump(),
            "new_locator": new.model_dump(),
            "old_fingerprint": step.fingerprint,
            "new_fingerprint": stored(picked),
            "chosen_is_truth": bool(picked.get("_truth")),  # evaluation only
            "before_shot": before,
            "heal_ms": int((time.time() - t0) * 1000),
            **choice_info,
        }
    )
    page.locator(CANDIDATES).nth(index).click()
    page.wait_for_load_state("domcontentloaded")
    # the remaining steps, every original assertion included, run unchanged; a failure there is
    # reported as a genuine failure and the heal is discarded (see run())
