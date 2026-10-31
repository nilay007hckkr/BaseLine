"""Read-only, cached GitHub REST reader. Fetches a public repository's issues, pull requests and
comments page by page into data/forge/, stops before the rate limit or the per-run request cap,
and resumes on the next run. It never writes to GitHub."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

API = "https://api.github.com"
KINDS = ("issues", "pulls", "issue_comments", "review_comments")
_PATHS = {
    "issues": "issues?state=all",
    "pulls": "pulls?state=all",
    # newest first: list endpoints stop paginating after about 1,000 items, so the recent
    # (most relevant) discussion is what fits
    "issue_comments": "issues/comments?sort=created&direction=desc",
    "review_comments": "pulls/comments?sort=created&direction=desc",
}
PER_PAGE = 100


@dataclass
class ForgeStatus:
    complete: dict[str, bool] = field(default_factory=lambda: dict.fromkeys(KINDS, False))
    pages: dict[str, int] = field(default_factory=lambda: dict.fromkeys(KINDS, 0))
    requests_this_run: int = 0
    stopped: str | None = None
    rate_reset_at: int | None = None
    limited: list[str] = field(default_factory=list)  # lists cut off by GitHub's pagination limit

    @property
    def done(self) -> bool:
        return all(self.complete.values())


class GitHubCache:
    def __init__(
        self,
        owner: str,
        repo: str,
        cache_dir: Path,
        token: str = "",
        max_requests: int = 50,
        client: httpx.Client | None = None,
    ) -> None:
        self.owner, self.repo = owner, repo
        self.dir = cache_dir / f"{owner}__{repo}"
        self.token = token
        self.max_requests = max_requests
        self.client = client

    @property
    def _state_path(self) -> Path:
        return self.dir / "state.json"

    def status(self) -> ForgeStatus:
        status = ForgeStatus()
        if self._state_path.exists():
            saved = json.loads(self._state_path.read_text())
            status.complete.update(saved.get("complete", {}))
        for kind in KINDS:
            status.pages[kind] = len(list(self.dir.glob(f"{kind}-*.json")))
        return status

    def fetch(self) -> ForgeStatus:
        self.dir.mkdir(parents=True, exist_ok=True)
        status = self.status()
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "hindsight-ingest"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        client = self.client or httpx.Client(timeout=30, headers=headers)
        try:
            for kind in KINDS:
                if status.complete[kind] or status.stopped:
                    continue
                page = 1
                while True:
                    path = self.dir / f"{kind}-{page:04d}.json"
                    if path.exists():
                        page += 1
                        continue
                    if status.requests_this_run >= self.max_requests:
                        status.stopped = f"request cap of {self.max_requests} per run reached"
                        break
                    sep = "&" if "?" in _PATHS[kind] else "?"
                    url = f"{API}/repos/{self.owner}/{self.repo}/{_PATHS[kind]}{sep}per_page={PER_PAGE}&page={page}"
                    try:
                        resp = client.get(url, headers=headers)
                    except httpx.HTTPError as exc:
                        # offline or a dropped connection: keep what was fetched and go on with
                        # the git history; the next connect resumes from the cache
                        status.stopped = f"GitHub not reachable ({type(exc).__name__})"
                        break
                    status.requests_this_run += 1
                    remaining = int(resp.headers.get("x-ratelimit-remaining", "1"))
                    reset = resp.headers.get("x-ratelimit-reset")
                    if resp.status_code in (403, 429):
                        status.stopped = "GitHub rate limit reached"
                        status.rate_reset_at = int(reset) if reset else None
                        break
                    if resp.status_code == 422:
                        # GitHub's pagination limit for list endpoints: this list is as complete
                        # as the API allows; move on to the next one
                        status.complete[kind] = True
                        status.limited.append(kind)
                        break
                    if resp.status_code != 200:
                        status.stopped = f"GitHub returned HTTP {resp.status_code} for {kind}"
                        break
                    items: list[dict[str, Any]] = resp.json()
                    path.write_text(json.dumps(items))
                    status.pages[kind] = page
                    if len(items) < PER_PAGE:
                        status.complete[kind] = True
                        break
                    page += 1
                    if remaining <= 1:
                        status.stopped = "GitHub rate limit reached"
                        status.rate_reset_at = int(reset) if reset else None
                        break
        finally:
            if self.client is None:
                client.close()
        self._state_path.write_text(
            json.dumps({"complete": status.complete, "updated_at": int(time.time())})
        )
        return status

    def fetch_issues(self, numbers: list[int], budget: int) -> tuple[int, str | None]:
        """Fetch single issues by number: the ones the history explicitly links to ("Fixes #N")
        that the list endpoints did not reach (they stop after about 1,000 items). Cached as
        linked-<n>.json; at most `budget` requests. Returns (fetched, why it stopped or None)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "hindsight-ingest"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        client = self.client or httpx.Client(timeout=30, headers=headers)
        fetched, stopped = 0, None
        try:
            for number in numbers:
                path = self.dir / f"linked-{number:06d}.json"
                if path.exists():
                    continue
                if fetched >= budget:
                    stopped = f"request budget of {budget} reached"
                    break
                url = f"{API}/repos/{self.owner}/{self.repo}/issues/{number}"
                try:
                    resp = client.get(url, headers=headers)
                except httpx.HTTPError as exc:
                    stopped = f"GitHub not reachable ({type(exc).__name__})"
                    break
                fetched += 1
                if resp.status_code in (403, 429):
                    stopped = "GitHub rate limit reached"
                    break
                if resp.status_code in (404, 410):
                    path.write_text("[]")  # deleted or moved: do not ask again
                    continue
                if resp.status_code != 200:
                    stopped = f"GitHub returned HTTP {resp.status_code} for issue #{number}"
                    break
                path.write_text(json.dumps([resp.json()]))
        finally:
            if self.client is None:
                client.close()
        return fetched, stopped

    def pull_request(self, number: int, max_pages: int = 3) -> dict[str, Any]:
        """One pull request's title, state and changed files (read-only; cached as
        prcheck-<n>.json, refreshed while the PR is open). Raises LookupError when GitHub has no
        such PR and RuntimeError when GitHub cannot be read."""
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"prcheck-{number:06d}.json"
        if path.exists():
            cached: dict[str, Any] = json.loads(path.read_text())
            if cached.get("state") != "open":
                return cached
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "hindsight-ingest"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        client = self.client or httpx.Client(timeout=30, headers=headers)
        base = f"{API}/repos/{self.owner}/{self.repo}/pulls/{number}"
        try:
            pr = self._get(client, base, headers)
            files: list[dict[str, Any]] = []
            for page in range(1, max_pages + 1):
                batch = self._get(client, f"{base}/files?per_page={PER_PAGE}&page={page}", headers)
                files += batch
                if len(batch) < PER_PAGE:
                    break
        except (httpx.HTTPError, RuntimeError) as exc:
            # offline or rate-limited: an earlier copy of this PR is better than nothing
            if path.exists():
                return json.loads(path.read_text())
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"GitHub not reachable ({type(exc).__name__})") from exc
        finally:
            if self.client is None:
                client.close()
        out = {
            "number": number,
            "title": pr.get("title") or "",
            "state": "merged" if pr.get("merged_at") else pr.get("state") or "",
            "url": pr.get("html_url")
            or f"https://github.com/{self.owner}/{self.repo}/pull/{number}",
            "author": (pr.get("user") or {}).get("login") or "",
            "files": [f["filename"] for f in files if f.get("filename")],
            "added": [f["filename"] for f in files if f.get("status") == "added"],
            "truncated": len(files) >= PER_PAGE * max_pages,
            "fetched_at": int(time.time()),
        }
        path.write_text(json.dumps(out))
        return out

    @staticmethod
    def _get(client: httpx.Client, url: str, headers: dict[str, str]) -> Any:
        resp = client.get(url, headers=headers)
        if resp.status_code == 404:
            raise LookupError("GitHub has no such pull request")
        if resp.status_code in (403, 429):
            raise RuntimeError("GitHub rate limit reached; try again later or set GITHUB_TOKEN")
        if resp.status_code != 200:
            raise RuntimeError(f"GitHub returned HTTP {resp.status_code}")
        return resp.json()

    def items(self, kind: str) -> Iterator[dict[str, Any]]:
        seen: set[Any] = set()
        for path in sorted(self.dir.glob(f"{kind}-*.json")):
            for item in json.loads(path.read_text()):
                ident = item.get("id")
                if ident in seen:
                    continue
                seen.add(ident)
                yield item


def status_dict(status: ForgeStatus) -> dict[str, Any]:
    return asdict(status) | {"done": status.done}
