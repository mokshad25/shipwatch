import asyncio

from shipwatch_common.models import PullRequest, WebhookEvent
from sqlalchemy import func, select


async def count(db, model) -> int:
    async with db() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def test_same_delivery_twice_stores_one_row(db, send, pr_payload):
    first = await send(pr_payload(), delivery="d-1")
    second = await send(pr_payload(), delivery="d-1")

    assert (first.status_code, first.json()) == (202, {"status": "accepted"})
    assert (second.status_code, second.json()) == (200, {"status": "duplicate"})
    assert await count(db, WebhookEvent) == 1


async def test_duplicate_delivery_changes_nothing(db, send, pr_payload):
    await send(pr_payload(title="Original"), delivery="d-1")
    # A replay with different content under the same delivery id must not overwrite anything.
    resp = await send(pr_payload(title="Tampered", head_sha="f" * 40), delivery="d-1")

    assert resp.status_code == 200
    async with db() as session:
        pr = await session.get(PullRequest, 1001)
        event = await session.scalar(select(WebhookEvent))
    assert (pr.title, pr.head_sha) == ("Original", "a" * 40)
    assert event.payload["pull_request"]["title"] == "Original"


async def test_concurrent_duplicates_store_one_row(db, send, pr_payload):
    responses = await asyncio.gather(*(send(pr_payload(), delivery="d-race") for _ in range(5)))

    assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 202]
    assert await count(db, WebhookEvent) == 1
    assert await count(db, PullRequest) == 1


async def test_distinct_deliveries_are_all_stored(db, send, pr_payload):
    responses = await asyncio.gather(*(send(pr_payload(), delivery=f"d-{i}") for i in range(5)))

    assert all(r.status_code == 202 for r in responses)
    assert await count(db, WebhookEvent) == 5
    assert await count(db, PullRequest) == 1
