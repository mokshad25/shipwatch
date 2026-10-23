"""Which errors are worth retrying, and how long to wait between attempts."""

import json
import random

from pydantic import ValidationError
from sqlalchemy.exc import DataError, IntegrityError


class InjectedFailure(ConnectionError):
    """Raised on purpose when FAIL_RATE > 0, to exercise retries and the DLQ. Retryable."""


# The message itself is bad: retrying can only fail the same way, so it goes straight to the DLQ.
NON_RETRYABLE: tuple[type[BaseException], ...] = (
    ValidationError,  # envelope doesn't match the schema
    json.JSONDecodeError,
    UnicodeDecodeError,
    DataError,  # Postgres rejected a value (too long, wrong type)
    IntegrityError,
)


def is_retryable(exc: BaseException) -> bool:
    """Timeouts, dropped connections and anything unexpected are retried (bounded by max_attempts);
    only errors that prove the message itself is bad are not."""
    return not isinstance(exc, NON_RETRYABLE)


def backoff_delay(attempt: int, base: float, cap: float, rng: random.Random | None = None) -> float:
    """Exponential backoff with full jitter: uniform in [0, min(cap, base * 2^(attempt-1))].

    `attempt` is the number of the attempt that just failed (1-based). Jitter spreads retries out so
    many consumers failing together don't hammer a recovering dependency in lockstep.
    """
    ceiling = min(cap, base * 2 ** (attempt - 1))
    return (rng or random).uniform(0, ceiling)
