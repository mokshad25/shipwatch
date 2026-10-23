"""Redis status keys that let the worker skip events it has already processed (docs/SPEC_W2.md).

Per event: `<prefix>evt:<event_id>` = `processing:<token>` (short lease) -> `done` (7 days).
Redis is a fast path, not the source of truth: `processed_events.event_id` is the primary key, so
a duplicate that slips past Redis is still caught in Postgres.
"""

import uuid
from enum import StrEnum

from redis.asyncio import Redis

DONE = "done"

# Delete the key only if it still holds our lease (not one taken over after ours expired).
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


# Push the lease's expiry out, again only if it is still ours.
_EXTEND = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('PEXPIRE', KEYS[1], ARGV[2]) end
return 0
"""


class Claim(StrEnum):
    CLAIMED = "claimed"  # we hold the lease: process it
    DONE = "done"  # already processed: skip and commit
    IN_PROGRESS = "in_progress"  # someone else's lease (most likely a crashed attempt): wait


class EventGate:
    def __init__(
        self, redis: Redis, *, prefix: str, lease_seconds: int, done_ttl_seconds: int
    ) -> None:
        self.redis = redis
        self.prefix = prefix
        self.lease_seconds = lease_seconds
        self.done_ttl_seconds = done_ttl_seconds

    def key(self, event_id: uuid.UUID) -> str:
        return f"{self.prefix}evt:{event_id}"

    async def claim(self, event_id: uuid.UUID) -> tuple[Claim, str | None]:
        """Try to take the processing lease. Returns the claim and, if claimed, our lease token."""
        token = f"processing:{uuid.uuid4().hex}"
        key = self.key(event_id)
        if await self.redis.set(key, token, nx=True, ex=self.lease_seconds):
            return Claim.CLAIMED, token
        current = await self.redis.get(key)
        if current is None:  # expired between SET and GET: try once more
            if await self.redis.set(key, token, nx=True, ex=self.lease_seconds):
                return Claim.CLAIMED, token
            current = await self.redis.get(key)
        return (Claim.DONE if current == DONE.encode() else Claim.IN_PROGRESS), None

    async def lease_remaining(self, event_id: uuid.UUID) -> float:
        ttl = await self.redis.pttl(self.key(event_id))
        return max(ttl, 0) / 1000

    async def extend(self, event_id: uuid.UUID, token: str) -> bool:
        """Renew our lease for another `lease_seconds`. False if it is no longer ours."""
        return bool(
            await self.redis.eval(
                _EXTEND, 1, self.key(event_id), token, int(self.lease_seconds * 1000)
            )
        )

    async def mark_done(self, event_id: uuid.UUID) -> None:
        await self.redis.set(self.key(event_id), DONE, ex=self.done_ttl_seconds)

    async def release(self, event_id: uuid.UUID, token: str) -> None:
        """Give the lease back after a failed attempt so the retry (or a replay) can claim it."""
        await self.redis.eval(_RELEASE, 1, self.key(event_id), token)
