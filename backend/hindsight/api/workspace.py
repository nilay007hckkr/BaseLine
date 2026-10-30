"""Workspace mode: an empty instance where a visitor connects their own GitHub repository.

Each repository gets its own folder under the workspace root (database, clone, GitHub cache,
uploads, evaluation results), so nothing mixes with the demo or with another repository.
`active.json` names the repository on screen. Connecting runs ingest and then the impact replay
evaluation (the same protocol as the demo's) in one background thread; the dashboard polls status.
Only public https://github.com/<owner>/<name> URLs are accepted."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from hindsight.core.config import Settings
from hindsight.core.db import connect
from hindsight.core.llm import scrub
from hindsight.core.schemas import WorkspaceStatus

_URL = re.compile(r"^https://github\.com/([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*?)(?:\.git)?/?$")

# (start of an ingest log line, what the dashboard shows, progress once reached)
STAGES: tuple[tuple[str, str, float], ...] = (
    ("cloning", "Cloning", 0.05),
    ("using existing clone", "Cloning", 0.05),
    ("reading git history", "Reading git history", 0.15),
    ("reading public GitHub", "Reading issues and pull requests", 0.35),
    ("  GitHub cache", "Linking everything", 0.75),
    ("done in", "Measuring accuracy", 0.85),
)

Ingest = Callable[..., dict[str, Any]]
Evaluate = Callable[[sqlite3.Connection, Path], bool]


def parse_repo_url(url: str) -> tuple[str, str]:
    m = _URL.match(url.strip())
    if not m:
        raise ValueError("use a public GitHub URL like https://github.com/owner/name")
    return m.group(1), m.group(2)


def _default_ingest(settings: Settings, repo_url: str, log: Callable[[str], None]) -> Any:
    from hindsight.brain.ingest import ingest

    return ingest(settings, repo_url, log=log)


def _default_evaluate(conn: sqlite3.Connection, out_dir: Path) -> bool:
    """The impact replay on this repository's own history; False when there is too little."""
    from hindsight.eval.impact_replay import queries, run

    if not queries(conn, 0.15):
        return False
    run(conn, out_dir, command="connected from the dashboard (impact replay)")
    return True


@dataclass
class _Job:
    repo: str
    stage: str = "Starting"
    progress: float = 0.0
    error: str | None = None
    running: bool = True


class Workspace:
    def __init__(
        self,
        base: Settings,
        ingest_fn: Ingest = _default_ingest,
        evaluate_fn: Evaluate = _default_evaluate,
    ) -> None:
        self.root = base.data_dir
        self.ingest_fn, self.evaluate_fn = ingest_fn, evaluate_fn
        self._lock = threading.Lock()
        self._job: _Job | None = None
        self._thread: threading.Thread | None = None
        self._measure_note: str | None = None
        self._active = self._read_active()

    # ---- which repository is on screen ----
    def _read_active(self) -> dict[str, str] | None:
        try:
            data = json.loads((self.root / "active.json").read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) and data.get("slug") else None

    def _folder(self, slug: str) -> Path:
        return self.root / "repos-by-name" / slug

    def settings_for(self, base: Settings, slug: str, url: str) -> Settings:
        folder = self._folder(slug)
        return replace(
            base,
            data_dir=folder,
            db_path=folder / "hindsight.db",
            results_dir=folder / "results",
            repo_url=url,
            graph_backend="sqlite",  # Neo4j holds the demo graph
        )

    def current(self, base: Settings) -> Settings:
        """Settings for the repository on screen (an empty database before the first connect)."""
        with self._lock:
            active = dict(self._active) if self._active else None
        if active:
            return self.settings_for(base, active["slug"], active["url"])
        empty = self.root / "empty"
        return replace(
            base,
            data_dir=empty,
            db_path=empty / "hindsight.db",
            results_dir=empty / "results",
            graph_backend="sqlite",
        )

    def repo(self) -> str | None:
        with self._lock:
            return self._active["web"] if self._active else None

    # ---- connecting ----
    def status(self) -> WorkspaceStatus:
        with self._lock:
            job = self._job
            active = dict(self._active) if self._active else None
        measured = False
        if active:
            measured = any((self._folder(active["slug"]) / "results").glob("impact-*.json"))
        return WorkspaceStatus(
            repo=active["web"] if active else None,
            job="idle" if job is None else "running" if job.running else "failed",
            job_repo=job.repo if job else None,
            stage=job.stage if job else "",
            progress=job.progress if job else 0.0,
            error=job.error if job else None,
            measured=measured,
            measure_note=self._measure_note,
        )

    def connect(self, base: Settings, url: str) -> WorkspaceStatus:
        owner, name = parse_repo_url(url)
        web = f"https://github.com/{owner}/{name}"
        with self._lock:
            if self._job and self._job.running:
                raise RuntimeError(f"already connecting {self._job.repo}")
            self._job = _Job(repo=web)
        slug = f"{owner}__{name}"
        settings = self.settings_for(base, slug, f"{web}.git")
        self._thread = threading.Thread(
            target=self._run, args=(settings, slug, web), name=f"connect-{slug}", daemon=True
        )
        self._thread.start()
        return self.status()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _log(self, line: str) -> None:
        for prefix, stage, progress in STAGES:
            if line.startswith(prefix):
                with self._lock:
                    if self._job:
                        self._job.stage, self._job.progress = stage, progress
                return

    def _run(self, settings: Settings, slug: str, web: str) -> None:
        try:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            self.ingest_fn(settings, settings.repo_url, log=self._log)
            self._set_active({"slug": slug, "url": settings.repo_url, "web": web})
            self._log("done in")
        except Exception as exc:  # the job reports any failure to the dashboard
            with self._lock:
                if self._job:
                    self._job.running = False
                    self._job.error = scrub(str(exc))[:300] or type(exc).__name__
            return
        self._measure_note = self._measure(settings)
        with self._lock:
            self._job = None

    def _measure(self, settings: Settings) -> str | None:
        """Run the accuracy replay; a failure here leaves the repository usable, just unmeasured."""
        conn = connect(settings.db_path)
        try:
            if not self.evaluate_fn(conn, settings.results_dir):
                return "too little history to measure (needs commits that change 2+ files)"
        except Exception as exc:  # reported on the Proof page, the dashboard still works
            return f"not measured: {scrub(str(exc))[:200] or type(exc).__name__}"
        finally:
            conn.close()
        return None

    def _set_active(self, active: dict[str, str]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "active.json").write_text(json.dumps(active, indent=2) + "\n")
        with self._lock:
            self._active = active
