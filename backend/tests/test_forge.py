"""The read-only GitHub reader, against a fake GitHub (httpx MockTransport). FIXTURE data only."""

import httpx

from hindsight.brain.forge import GitHubCache


def fake_github(limit_kind: str, rate_remaining: int = 50):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        page = int(request.url.params.get("page", "1"))
        headers = {"x-ratelimit-remaining": str(rate_remaining), "x-ratelimit-reset": "2000000000"}
        if limit_kind in request.url.path and "comments" not in request.url.path and page == 2:
            return httpx.Response(422, json={"message": "pagination is limited"}, headers=headers)
        items = [
            {"id": page * 1000 + i, "number": page * 1000 + i}
            for i in range(100 if page == 1 else 3)
        ]
        return httpx.Response(200, json=items, headers=headers)

    return handler, seen


def test_pagination_limit_marks_the_list_done_and_moves_on(tmp_path):
    handler, seen = fake_github("/issues")
    cache = GitHubCache(
        "acme", "shop", tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    status = cache.fetch()
    assert status.complete["issues"] and status.limited == ["issues"]
    assert status.complete["pulls"]  # the run continued past the limited list
    assert any("/pulls?" in u for u in seen)
    assert len(list(cache.items("issues"))) == 100


def test_request_cap_stops_and_the_next_run_resumes(tmp_path):
    handler, seen = fake_github("/none")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = GitHubCache("acme", "shop", tmp_path, max_requests=1, client=client).fetch()
    assert first.stopped and "request cap" in first.stopped and first.pages["issues"] == 1
    second = GitHubCache("acme", "shop", tmp_path, max_requests=50, client=client).fetch()
    assert second.complete["issues"] and second.done
    issue_page_1 = [u for u in seen if "/issues?" in u and httpx.URL(u).params.get("page") == "1"]
    assert len(issue_page_1) == 1  # the second run read page 1 from the cache


def test_rate_limit_is_reported_with_reset_time(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "2000000000"}
        )

    status = GitHubCache(
        "acme", "shop", tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler))
    ).fetch()
    assert status.stopped == "GitHub rate limit reached" and status.rate_reset_at == 2000000000


def test_a_dropped_connection_stops_the_fetch_without_failing_the_import(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network is unreachable", request=request)

    status = GitHubCache(
        "acme", "shop", tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler))
    ).fetch()
    assert status.stopped == "GitHub not reachable (ConnectError)"
    assert not status.done
