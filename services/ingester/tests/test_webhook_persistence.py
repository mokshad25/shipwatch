from datetime import UTC, datetime

import pytest
from shipwatch_common.models import PullRequest, Repo, WebhookEvent
from sqlalchemy import func, select


async def count(db, model) -> int:
    async with db() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def test_pull_request_event_stores_repo_pr_and_event(db, send, pr_payload):
    resp = await send(pr_payload(), delivery="d-opened")

    assert resp.status_code == 202
    async with db() as session:
        repo = await session.get(Repo, 501)
        pr = await session.get(PullRequest, 1001)
        event = await session.scalar(select(WebhookEvent))

    assert repo.full_name == "octo/sandbox"
    assert (pr.repo_id, pr.number, pr.title, pr.author) == (501, 7, "Add retries", "octocat")
    assert (pr.state, pr.head_sha, pr.base_branch) == ("open", "a" * 40, "main")
    assert pr.created_at == datetime(2026, 10, 26, 10, tzinfo=UTC)
    assert pr.merged_at is None
    assert (event.delivery_id, event.event_type, event.action) == (
        "d-opened",
        "pull_request",
        "opened",
    )
    assert event.payload["pull_request"]["id"] == 1001
    assert event.received_at is not None


async def test_later_events_update_the_same_pull_request(db, send, pr_payload):
    await send(pr_payload(action="opened"))
    await send(pr_payload(action="synchronize", head_sha="b" * 40, title="Add retries v2"))
    await send(
        pr_payload(
            action="closed", state="closed", head_sha="b" * 40, merged_at="2026-10-27T09:30:00Z"
        )
    )

    assert await count(db, Repo) == 1
    assert await count(db, PullRequest) == 1
    assert await count(db, WebhookEvent) == 3
    async with db() as session:
        pr = await session.get(PullRequest, 1001)
    assert (pr.state, pr.head_sha) == ("closed", "b" * 40)
    assert pr.merged_at == datetime(2026, 10, 27, 9, 30, tzinfo=UTC)


async def test_non_pull_request_event_is_accepted_but_not_stored(db, send):
    resp = await send({"zen": "Design for failure."}, event="ping")

    assert resp.status_code == 202
    assert await count(db, WebhookEvent) == 0


async def test_failure_mid_transaction_stores_nothing(db, send, pr_payload):
    payload = pr_payload()
    del payload["pull_request"]["head"]  # breaks the PR upsert after the event row is flushed

    # ASGITransport re-raises app exceptions; behind uvicorn this is a 500.
    with pytest.raises(KeyError):
        await send(payload)

    assert await count(db, WebhookEvent) == 0
    assert await count(db, Repo) == 0
