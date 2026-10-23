"""Dead-letter records: the original message plus why and how often it failed."""

import base64
import json
from datetime import datetime
from typing import Any

from confluent_kafka import Message


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode("utf-8", errors="replace")


def dead_letter_record(
    msg: Message,
    exc: BaseException,
    *,
    attempts: int,
    retryable: bool,
    first_failed_at: datetime,
    last_failed_at: datetime,
) -> dict[str, Any]:
    raw = msg.value() or b""
    try:
        value, encoding = json.loads(raw), "json"
    except ValueError:  # not JSON (or not UTF-8): keep the exact bytes
        value, encoding = base64.b64encode(raw).decode(), "base64"
    return {
        "original": {
            "topic": msg.topic(),
            "partition": msg.partition(),
            "offset": msg.offset(),
            "key": _text(msg.key()),
            "headers": {k: _text(v) for k, v in (msg.headers() or [])},
            "value": value,
            "value_encoding": encoding,
        },
        "error": {
            "type": type(exc).__name__,
            "message": str(exc)[:2000],
            "retryable": retryable,
            "attempts": attempts,
            "first_failed_at": first_failed_at.isoformat(),
            "last_failed_at": last_failed_at.isoformat(),
        },
    }


def original_value(record: dict[str, Any]) -> bytes:
    """The original message bytes back from a dead-letter record (used by replay)."""
    original = record["original"]
    if original["value_encoding"] == "base64":
        return base64.b64decode(original["value"])
    return json.dumps(original["value"]).encode()
