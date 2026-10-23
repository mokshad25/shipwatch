from functools import lru_cache

from pydantic import Field
from shipwatch_common.settings import ServiceSettings


class WorkerSettings(ServiceSettings):
    consumer_group: str = "worker"
    poll_timeout: float = 1.0
    # Static group membership: a restarted worker with the same id gets its partitions straight
    # back instead of waiting for the dead member's session to time out. Must be unique per
    # running replica. None = dynamic membership.
    consumer_instance_id: str | None = None
    session_timeout_ms: int = 10_000
    max_attempts: int = 3
    backoff_base: float = 0.5  # seconds before the 2nd attempt (upper bound, jittered)
    backoff_max: float = 5.0
    # Probability (0..1) that an attempt fails with an injected, retryable error. Testing only.
    fail_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    # How long a "processing" key blocks redelivered copies of an event. Must exceed the slowest
    # normal processing time; after a crash, that event's partition waits up to this long.
    processing_lease_seconds: int = 20
    done_ttl_seconds: int = 7 * 24 * 3600


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()
