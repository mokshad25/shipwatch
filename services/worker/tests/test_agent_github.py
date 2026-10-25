"""GitHubClient: headers, typed errors, Redis caching and pagination (docs/SPEC_W3.md)."""

import time

import httpx
import pytest
from agent.cache import ResponseCache
from agent.github import GitHubClient, GitHubError, GitHubRateLimited
from redis.asyncio import Redis

PR = "/repos/encode/httpx/pulls/3174"


async def test_sends_api_version_and_token(github, gh_api, load_github):
    route = gh_api.get(PR).respond(json=load_github("pr_3174.json"))

    body = await github.get_json(PR)

    assert body["number"] == 3174
    sent = route.calls.last.request.headers
    assert sent["authorization"] == "Bearer test-token"
    assert sent["accept"] == "application/vnd.github+json"
    assert sent["x-github-api-version"] == "2022-11-28"


async def test_no_token_sends_no_authorization(github, gh_api):
    route = gh_api.get(PR).respond(json={})
    anonymous = GitHubClient(github.http, github.cache, token=None)

    await anonymous.get_json(PR)

    assert "authorization" not in route.calls.last.request.headers


async def test_not_found_raises_github_error(github, gh_api):
    gh_api.get(PR).respond(404, json={"message": "Not Found"})

    with pytest.raises(GitHubError) as err:
        await github.get_json(PR)

    assert not isinstance(err.value, GitHubRateLimited)
    assert (err.value.status, err.value.message) == (404, "Not Found")


async def test_plain_403_is_not_a_rate_limit(github, gh_api):
    gh_api.get(PR).respond(403, json={"message": "Resource not accessible"})

    with pytest.raises(GitHubError) as err:
        await github.get_json(PR)

    assert not isinstance(err.value, GitHubRateLimited)


async def test_primary_rate_limit(github, gh_api):
    reset = int(time.time()) + 60
    gh_api.get(PR).respond(
        403,
        json={"message": "API rate limit exceeded"},
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)},
    )

    with pytest.raises(GitHubRateLimited) as err:
        await github.get_json(PR)

    assert 50 < err.value.retry_after <= 60


@pytest.mark.parametrize("status", [403, 429])
async def test_secondary_rate_limit_uses_retry_after(github, gh_api, status):
    gh_api.get(PR).respond(
        status, json={"message": "secondary rate limit"}, headers={"retry-after": "30"}
    )

    with pytest.raises(GitHubRateLimited) as err:
        await github.get_json(PR)

    assert err.value.retry_after == 30


async def test_second_call_is_served_from_redis_for_ten_minutes(github, gh_api, load_github):
    route = gh_api.get(PR).respond(json=load_github("pr_3174.json"))

    first = await github.get_json(PR)
    second = await github.get_json(PR)

    assert first == second
    assert route.call_count == 1
    ttl = await github.cache.redis.ttl(github.cache.key(PR, None))
    assert 590 <= ttl <= 600


async def test_cache_key_depends_on_params(github, gh_api):
    route = gh_api.get("/repos/encode/httpx/commits").respond(json=[])

    await github.get_json("/repos/encode/httpx/commits", {"path": "a.py"})
    await github.get_json("/repos/encode/httpx/commits", {"path": "b.py"})

    assert route.call_count == 2


async def test_errors_are_not_cached(github, gh_api):
    route = gh_api.get(PR).mock(
        side_effect=[
            httpx.Response(502, json={"message": "Bad Gateway"}),
            httpx.Response(200, json={}),
        ]
    )

    with pytest.raises(GitHubError):
        await github.get_json(PR)
    assert await github.get_json(PR) == {}
    assert route.call_count == 2


async def test_redis_down_falls_through_to_github(gh_api):
    dead = Redis.from_url("redis://localhost:1/0", socket_connect_timeout=0.2)
    cache = ResponseCache(dead, prefix="test:", ttl_seconds=600)
    async with GitHubClient(httpx.AsyncClient(base_url="https://api.github.com"), cache) as gh:
        route = gh_api.get(PR).respond(json={"number": 3174})

        assert (await gh.get_json(PR))["number"] == 3174
        assert (await gh.get_json(PR))["number"] == 3174

    assert route.call_count == 2
    await dead.aclose()


async def test_get_pages_follows_link_until_max_pages(github, gh_api):
    files = "/repos/encode/httpx/pulls/1/files"
    nxt = "https://api.github.com/repositories/1/pulls/1/files?per_page=100&page={}"
    gh_api.get(files).respond(json=[1, 2], headers={"link": f'<{nxt.format(2)}>; rel="next"'})
    gh_api.get(nxt.format(2)).respond(json=[3], headers={"link": f'<{nxt.format(3)}>; rel="next"'})
    page3 = gh_api.get(nxt.format(3)).respond(json=[4])

    assert await github.get_pages(files, {"per_page": 100}, max_pages=2) == ([1, 2, 3], True)
    assert page3.call_count == 0
    assert await github.get_pages(files, {"per_page": 100}, max_pages=5) == ([1, 2, 3, 4], False)
