"""Read a repository's history with plain git: commits, changed files with rename lineage,
and the classification filters that every statistic depends on (DESIGN §1)."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# %aN/%aE apply the repository's .mailmap, so one person with several addresses is one author
_FMT = "%x1e%H%x1f%P%x1f%aN%x1f%aE%x1f%at%x1f%s%x1f%b%x1f"
_NUMSTAT = re.compile(r"^(\d+|-)\t(\d+|-)\t(.+)$")
_BRACE = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")
_BLAME = re.compile(r"^([0-9a-f]{40}) \d+ \d+")

BOT = re.compile(r"\[bot\]|renovate|dependabot|github-actions|greenkeeper|mergify", re.IGNORECASE)
GENERATED = re.compile(
    r"(\.pb\.go$|_pb2(_grpc)?\.pyi?$|(^|/)genproto/|(^|/)go\.sum$|(^|/)package-lock\.json$"
    r"|(^|/)yarn\.lock$|(^|/)pnpm-lock\.yaml$|\.lock$|(^|/)requirements\.txt$"
    r"|\.min\.(js|css)$|(^|/)vendor/|(^|/)node_modules/)"
)
SWEEP_FILES = 30


@dataclass
class FileChange:
    path: str
    old_path: str | None
    additions: int | None
    deletions: int | None


@dataclass
class RawCommit:
    sha: str
    parents: list[str]
    author: str
    email: str
    ts: int
    subject: str
    body: str
    files: list[FileChange] = field(default_factory=list)

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1

    @property
    def is_bot(self) -> bool:
        return bool(BOT.search(f"{self.author} {self.email}"))

    @property
    def is_sweep(self) -> bool:
        return len(self.files) > SWEEP_FILES

    @property
    def ok(self) -> bool:
        """Counts towards statistics: not a merge, bot or sweep commit."""
        return not (self.is_merge or self.is_bot or self.is_sweep)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout


def split_rename(path: str) -> tuple[str | None, str]:
    """numstat with -M writes renames as 'a/{old => new}/f' or 'old => new'."""
    m = _BRACE.match(path)
    if m:
        pre, old, new, post = m.groups()
        return (pre + old + post).replace("//", "/"), (pre + new + post).replace("//", "/")
    if " => " in path:
        old, new = path.split(" => ", 1)
        return old, new
    return None, path


def parse_log(text: str) -> list[RawCommit]:
    commits: list[RawCommit] = []
    for record in text.split("\x1e")[1:]:
        parts = record.split("\x1f")
        if len(parts) < 8:
            continue
        sha, parents, author, email, ts, subject, body, rest = parts[:8]
        commit = RawCommit(
            sha=sha,
            parents=parents.split(),
            author=author,
            email=email,
            ts=int(ts),
            subject=subject,
            body=body.strip(),
        )
        for line in rest.splitlines():
            m = _NUMSTAT.match(line.strip("\n"))
            if not m:
                continue
            add, dele, raw = m.groups()
            old, new = split_rename(raw)
            commit.files.append(
                FileChange(
                    path=new,
                    old_path=old,
                    additions=None if add == "-" else int(add),
                    deletions=None if dele == "-" else int(dele),
                )
            )
        commits.append(commit)
    return commits


def read_log(repo: Path) -> list[RawCommit]:
    """All commits reachable from HEAD, oldest first."""
    return parse_log(
        git(repo, "log", "--reverse", "-M", "--numstat", "--no-color", f"--format={_FMT}")
    )


def lineage_map(commits: list[RawCommit]) -> dict[str, str]:
    """Map every path a file ever had to its latest name, so statistics follow renames."""
    renamed: dict[str, str] = {}
    for commit in commits:
        for change in commit.files:
            if change.old_path and change.old_path != change.path:
                renamed[change.old_path] = change.path
    final: dict[str, str] = {}
    for start in renamed:
        path, seen = start, {start}
        while path in renamed and renamed[path] not in seen:
            path = renamed[path]
            seen.add(path)
        final[start] = path
    return final


def is_generated(path: str) -> bool:
    return bool(GENERATED.search(path))


def head_files(repo: Path) -> set[str]:
    return set(git(repo, "ls-files").splitlines())


def head_info(repo: Path) -> tuple[str, int]:
    sha, ts = git(repo, "log", "-1", "--format=%H %ct").split()
    return sha, int(ts)


def blame_commits(repo: Path, path: str, start: int, end: int) -> list[str]:
    """Commits that last touched lines start..end of path at HEAD, most lines first."""
    try:
        out = git(repo, "blame", "--porcelain", "-L", f"{start},{end}", "--", path)
    except subprocess.CalledProcessError:
        return []
    counts: dict[str, int] = {}
    for line in out.splitlines():
        m = _BLAME.match(line)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return sorted(counts, key=lambda sha: -counts[sha])
