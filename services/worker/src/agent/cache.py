"""Redis cache for GitHub GET responses (docs/SPEC_W3.md).

Fails open: if Redis is unreachable the lookup is a miss and the store is skipped, so a Redis outage
makes the tools slower, never broken.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from shipwatch_common.logging import get_logger

log = get_logger(__name__)


class ResponseCache:
    def __init__(self, redis: Redis, *, prefix: str, ttl_seconds: int) -> None:
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds

    def key(self, url: str, params: dict[str, Any] | None) -> str:
        raw = json.dumps([url, sorted((params or {}).items())], default=str)
        return f"{self.prefix}gh:{hashlib.sha256(raw.encode()).hexdigest()}"

    async def get(self, key: str) -> dict[str, Any] | None:
        try:
            value = await self.redis.get(key)
        except RedisError as exc:
            log.warning("github_cache_unavailable", op="get", error=str(exc))
            return None
        return None if value is None else json.loads(value)

    async def set(self, key: str, value: dict[str, Any]) -> None:
        try:
            await self.redis.set(key, json.dumps(value), ex=self.ttl_seconds)
        except RedisError as exc:
            log.warning("github_cache_unavailable", op="set", error=str(exc))


class FileResponseCache:
    """Same interface as ResponseCache, but JSON files that never expire: evals record GitHub
    responses once and replay them, so every run (and CI) sees identical tool output."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def key(self, url: str, params: dict[str, Any] | None) -> str:
        raw = json.dumps([url, sorted((params or {}).items())], default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    async def get(self, key: str) -> dict[str, Any] | None:
        path = self.directory / f"{key}.json"
        return json.loads(path.read_text()) if path.is_file() else None

    async def set(self, key: str, value: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.directory / f"{key}.json.tmp"
        tmp.write_text(json.dumps(value, sort_keys=True))
        tmp.replace(self.directory / f"{key}.json")
