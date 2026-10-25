"""Minimal GitHub REST client for the agent's tools: cached GETs, pagination, typed errors."""

import time
from typing import Any, Self

import httpx
from redis.asyncio import Redis
from shipwatch_common.logging import get_logger
from worker.settings import WorkerSettings

from agent.cache import ResponseCache

log = get_logger(__name__)

API_VERSION = "2022-11-28"


class GitHubError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status
        self.message = message


class GitHubRateLimited(GitHubError):
    def __init__(self, status: int, message: str, retry_after: float | None) -> None:
        super().__init__(status, message)
        self.retry_after = retry_after  # seconds until GitHub accepts requests again, if known


class GitHubClient:
    def __init__(self, http: httpx.AsyncClient, cache: ResponseCache, token: str | None = None):
        self.http = http
        self.cache = cache
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    @classmethod
    def from_settings(cls, settings: WorkerSettings, redis: Redis) -> Self:
        http = httpx.AsyncClient(
            base_url=settings.github_api_url, timeout=settings.github_timeout_seconds
        )
        cache = ResponseCache(
            redis, prefix=settings.redis_key_prefix, ttl_seconds=settings.tool_cache_ttl_seconds
        )
        token = settings.github_token.get_secret_value() if settings.github_token else None
        return cls(http, cache, token)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """One GET, cached: returns {"body": <json>, "next": <next page url or None>}."""
        key = self.cache.key(url, params)
        if (cached := await self.cache.get(key)) is not None:
            return cached
        response = await self.http.get(url, params=params, headers=self.headers)
        if response.is_error:
            raise _error(response)
        result = {"body": response.json(), "next": response.links.get("next", {}).get("url")}
        await self.cache.set(key, result)
        return result

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return (await self._get(path, params))["body"]

    async def get_pages(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_pages: int,
        items_key: str | None = None,
    ) -> tuple[list[Any], bool]:
        """Follow `Link: rel="next"` for up to `max_pages` pages.

        Returns all items and whether more pages were left unread. `items_key` picks the list out
        of an object body (e.g. "check_runs"); by default the body itself is the list.
        """
        items: list[Any] = []
        url: str | None = path
        for _ in range(max_pages):
            page = await self._get(url, params)
            body = page["body"]
            items.extend(body[items_key] if items_key else body)
            url = page["next"]
            params = None  # the next-page URL already carries the query
            if url is None:
                return items, False
        return items, True


def _error(response: httpx.Response) -> GitHubError:
    try:
        message = response.json().get("message", "")
    except ValueError:
        message = response.text[:200]
    headers = response.headers
    if response.status_code == 429 or (
        response.status_code == 403
        and (headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers)
    ):
        retry_after: float | None = None
        if "retry-after" in headers:
            retry_after = float(headers["retry-after"])
        elif "x-ratelimit-reset" in headers:
            retry_after = max(float(headers["x-ratelimit-reset"]) - time.time(), 0.0)
        log.warning("github_rate_limited", status=response.status_code, retry_after=retry_after)
        return GitHubRateLimited(response.status_code, message, retry_after)
    return GitHubError(response.status_code, message)
