"""PR check (PS 06 bonus): the impact summary for a proposed change, from a GitHub pull request or
a local branch. The ranking and every reason come from the impact check (brain/impact.py); this
module only collects the changed files and writes the summary as Markdown, ready to paste as a PR
comment. It never writes to GitHub."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from hindsight.brain.forge import GitHubCache
from hindsight.brain.impact import impact_report
from hindsight.core.config import Settings
from hindsight.core.db import get_meta
from hindsight.core.graphstore import GraphReader
from hindsight.core.schemas import ImpactItem, ImpactReport

TOP = 5


def pr_report(
    conn: sqlite3.Connection,
    settings: Settings,
    number: int,
    graph: GraphReader | None = None,
    cache: GitHubCache | None = None,
) -> ImpactReport:
    """Impact of GitHub pull request #number of the ingested repository. Raises LookupError for an
    unknown PR or repository and RuntimeError when GitHub cannot be read."""
    repo = get_meta(conn, "repo") or {}
    if not repo.get("owner"):
        raise LookupError("no repository ingested yet")
    cache = cache or GitHubCache(
        repo["owner"], repo["name"], settings.forge_dir, token=settings.github_token
    )
    pr = cache.pull_request(number)
    if not pr["files"]:
        raise LookupError(f"PR #{number} changes no files")
    report = impact_report(conn, files=pr["files"], graph=graph)
    n = len(pr["files"])
    notes = [
        f"PR #{number} ({pr['state']}): {pr['title']} · {n} file{'' if n == 1 else 's'} changed"
    ]
    if pr["truncated"]:
        notes.append(f"only the first {len(pr['files'])} changed files were read")
    if pr["added"]:
        notes.append(f"{len(pr['added'])} new file(s) have no history yet")
    return report.model_copy(update={"notes": notes + report.notes})


def branch_files(repo: Path, base: str, head: str = "HEAD") -> list[str]:
    """Files changed on `head` since it left `base` (git diff base...head)."""
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise LookupError(out.stderr.strip() or f"git diff {base}...{head} failed")
    return [line for line in out.stdout.splitlines() if line.strip()]


def _items(title: str, items: list[ImpactItem], code: bool = False) -> list[str]:
    if not items:
        return []
    lines = [f"**{title}**", ""]
    for it in items[:TOP]:
        name = f"`{it.target}`" if code else f"**{it.target}**"
        if it.url:
            name = f"[{name}]({it.url})"
        reasons = "; ".join(
            f"[{r.text}]({r.url}) ({r.provenance})" if r.url else f"{r.text} ({r.provenance})"
            for r in it.reasons[:2]
        )
        lines.append(f"- {name}: {reasons}" if reasons else f"- {name}")
    if len(items) > TOP:
        lines.append(f"- and {len(items) - TOP} more")
    return [*lines, ""]


def markdown(report: ImpactReport, title: str = "Hindsight impact check") -> str:
    """The summary as a PR comment. Every line comes from the report; nothing is added."""
    lines = [f"### {title}", ""]
    lines += [f"> {n}" for n in report.notes[:1]] + ([""] if report.notes else [])
    lines += _items("Services and components likely affected", report.components)
    lines += _items("Files that usually change with these", report.files, code=True)
    lines += _items("Tests to re-run", report.tests, code=True)
    lines += _items("Docs to re-read", report.docs)
    lines += _items("Runbooks", report.runbooks)
    lines += _items("People who know this code", report.people)
    if not (report.components or report.files or report.tests or report.docs):
        lines += ["Not found: no history or code links for these files.", ""]
    extra = report.notes[1:]
    if extra:
        lines += ["<details><summary>Notes</summary>", ""]
        lines += [f"- {n}" for n in extra] + ["", "</details>", ""]
    lines.append(
        "_Each reason comes from the project's history or code, with its provenance;"
        " no model wrote any of it._"
    )
    return "\n".join(lines) + "\n"
