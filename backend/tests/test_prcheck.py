"""PR check: a pull request's (or branch's) changed files → impact summary. FIXTURE data only;
GitHub is faked with httpx.MockTransport or a cached file."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from hindsight.api import app as app_module
from hindsight.brain.forge import GitHubCache
from hindsight.brain.prcheck import branch_files, markdown, pr_report


def _github(number: int, files: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not path.startswith(f"/repos/acme/shop/pulls/{number}"):
            return httpx.Response(404, json={"message": "Not Found"})
        if path.endswith("/files"):
            return httpx.Response(200, json=files)
        pr = {
            "title": "Settle refunds in USD",
            "state": "open",
            "merged_at": None,
            "html_url": f"https://github.com/acme/shop/pull/{number}",
            "user": {"login": "ana"},
        }
        return httpx.Response(200, json=pr)

    return handler


def _cache(fx, tmp_path, handler) -> GitHubCache:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GitHubCache("acme", "shop", tmp_path, client=client)


def test_a_pull_request_gets_an_impact_summary_with_reasons(fx, tmp_path):
    files = [
        {"filename": "src/checkoutservice/main.go", "status": "modified"},
        {"filename": "src/checkoutservice/refunds.go", "status": "added"},
    ]
    cache = _cache(fx, tmp_path, _github(77, files))
    conn = fx.conn()
    report = pr_report(conn, fx.settings, 77, cache=cache)
    assert report.notes[0].startswith("PR #77 (open): Settle refunds in USD · 2 files changed")
    assert any("1 new file(s) have no history yet" in n for n in report.notes)
    assert report.changed_files == ["src/checkoutservice/main.go", "src/checkoutservice/refunds.go"]
    assert report.components and report.components[0].reasons
    text = markdown(report)
    assert text.startswith("### Hindsight impact check")
    assert "**Services and components likely affected**" in text
    first = report.components[0]
    assert first.target in text and first.reasons[0].text in text
    assert "no model wrote any of it" in text


def test_an_unknown_pull_request_is_a_plain_lookup_error(fx, tmp_path):
    cache = _cache(fx, tmp_path, _github(77, []))
    with pytest.raises(LookupError):
        pr_report(fx.conn(), fx.settings, 5, cache=cache)


def test_an_open_pr_is_refetched_and_a_closed_one_is_read_from_the_cache(fx, tmp_path):
    seen: list[str] = []
    files = [{"filename": "src/checkoutservice/main.go", "status": "modified"}]
    inner = _github(77, files)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return inner(request)

    cache = _cache(fx, tmp_path, handler)
    cache.pull_request(77)
    cache.pull_request(77)
    assert len(seen) == 4  # open: asked again (PR + files, twice)
    saved = tmp_path / "acme__shop" / "prcheck-000077.json"
    saved.write_text(json.dumps({**json.loads(saved.read_text()), "state": "merged"}))
    cache.pull_request(77)
    assert len(seen) == 4  # merged: read from the cache


def test_the_api_returns_the_report_and_the_comment(fx):
    cache_dir = fx.settings.forge_dir / "acme__shop"
    cached = {
        "number": 12,
        "title": "Charge in USD",
        "state": "merged",
        "url": "https://github.com/acme/shop/pull/12",
        "author": "ana",
        "files": ["src/checkoutservice/main.go"],
        "added": [],
        "truncated": False,
        "fetched_at": 0,
    }
    (cache_dir / "prcheck-000012.json").write_text(json.dumps(cached))
    app_module.app.dependency_overrides[app_module.get_settings] = lambda: fx.settings
    try:
        c = TestClient(app_module.app)
        r = c.get("/api/impact/pr/12")
        assert r.status_code == 200 and r.json()["notes"][0].startswith("PR #12 (merged)")
        comment = c.get("/api/impact/pr/12/comment")
        assert comment.status_code == 200 and comment.text.startswith("### Hindsight impact check")
        assert c.get("/api/impact/pr/0").status_code == 422
    finally:
        app_module.app.dependency_overrides.clear()


def test_branch_mode_reads_the_files_changed_since_the_base(fx):
    files = branch_files(fx.repo, "HEAD~1")
    assert files and all(not f.startswith("/") for f in files)
    with pytest.raises(LookupError):
        branch_files(fx.repo, "no-such-branch")


def test_a_rate_limit_falls_back_to_the_cached_pr(fx, tmp_path):
    files = [{"filename": "src/checkoutservice/main.go", "status": "modified"}]
    _cache(fx, tmp_path, _github(77, files)).pull_request(77)  # cached while open

    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0"})

    pr = _cache(fx, tmp_path, limited).pull_request(77)
    assert pr["title"] == "Settle refunds in USD"
    with pytest.raises(RuntimeError):
        _cache(fx, tmp_path, limited).pull_request(78)  # nothing cached
