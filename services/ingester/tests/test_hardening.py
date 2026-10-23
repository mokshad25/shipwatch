"""W1·D6: malformed input, oversized bodies, structured logs, and the global 500 handler."""

import io
import json
import logging
import os

import pytest
from ingester import webhooks
from ingester.signature import sign
from shipwatch_common.logging import json_formatter
from shipwatch_common.models import WebhookEvent
from shipwatch_common.settings import get_settings
from sqlalchemy import func, select

SECRET = os.environ["GITHUB_WEBHOOK_SECRET"].encode()  # the test secret set by conftest
LIMIT = get_settings().max_body_bytes


def signed_headers(body: bytes, delivery: str = "d-1", event: str = "pull_request") -> dict:
    return {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": sign(SECRET, body),
    }


async def count_events(db) -> int:
    async with db() as session:
        return await session.scalar(select(func.count()).select_from(WebhookEvent))


@pytest.mark.parametrize(
    "body",
    [b"{not json", b"", b"\xff\xfe", b"[1, 2, 3]", b'"just a string"', b"[" * 100_000],
    ids=["broken", "empty", "not-utf8", "array", "string", "deeply-nested"],
)
async def test_malformed_json_is_400_and_stores_nothing(db, client, body):
    resp = await client.post("/webhooks/github", content=body, headers=signed_headers(body))

    assert resp.status_code == 400
    assert resp.json() == {"detail": "malformed JSON"}
    assert await count_events(db) == 0


async def test_missing_event_header_is_400(db, client):
    body = b"{}"
    headers = signed_headers(body)
    del headers["X-GitHub-Event"]

    resp = await client.post("/webhooks/github", content=body, headers=headers)

    assert resp.status_code == 400


async def test_body_over_limit_is_413(db, client):
    body = b" " * (LIMIT + 1)

    resp = await client.post("/webhooks/github", content=body, headers=signed_headers(body))

    assert resp.status_code == 413
    assert await count_events(db) == 0


async def test_streamed_body_over_limit_without_content_length_is_413(db, client):
    body = b" " * (LIMIT + 1)

    async def chunks():
        for i in range(0, len(body), 64 * 1024):
            yield body[i : i + 64 * 1024]

    resp = await client.post("/webhooks/github", content=chunks(), headers=signed_headers(body))

    assert resp.status_code == 413


async def test_body_at_limit_is_accepted(db, client):
    prefix = b'{"zen": "'
    body = prefix + b"x" * (LIMIT - len(prefix) - 2) + b'"}'
    assert len(body) == LIMIT

    resp = await client.post(
        "/webhooks/github", content=body, headers=signed_headers(body, event="ping")
    )

    assert resp.status_code == 202


@pytest.fixture
def log_lines():
    """Every log line emitted during the test, rendered exactly as in production (JSON)."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(json_formatter())
    root = logging.getLogger()
    root.addHandler(handler)
    yield lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
    root.removeHandler(handler)


async def test_every_log_line_of_a_request_has_delivery_id(db, send, pr_payload, log_lines):
    await send(pr_payload(), delivery="d-logged")

    lines = log_lines()
    assert {line["event"] for line in lines} >= {"webhook stored", "request"}
    assert all(line["delivery_id"] == "d-logged" for line in lines)
    request_line = next(line for line in lines if line["event"] == "request")
    assert request_line["status"] == 202
    assert request_line["path"] == "/webhooks/github"


async def test_unhandled_error_is_500_without_internals_and_logged(
    db, send, pr_payload, log_lines, monkeypatch
):
    async def boom(session, payload):
        raise RuntimeError("password=hunter2 at db.internal:5432")

    monkeypatch.setattr(webhooks, "upsert_pull_request", boom)

    resp = await send(pr_payload(), delivery="d-boom")

    assert resp.status_code == 500
    assert resp.json() == {"detail": "internal server error"}
    assert "hunter2" not in resp.text
    assert await count_events(db) == 0  # the transaction rolled back

    error = next(line for line in log_lines() if line["event"] == "unhandled error")
    assert error["level"] == "error"
    assert error["delivery_id"] == "d-boom"
    assert error["exception"][0]["exc_type"] == "RuntimeError"
    assert error["exception"][0]["frames"]  # full traceback in the log
