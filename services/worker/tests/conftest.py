import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest
import redis
from shipwatch_common.db import make_sessionmaker
from shipwatch_common.kafka import EventEnvelope, Producer
from shipwatch_common.models import Base, ProcessedEvent
from sqlalchemy import func, select, text
from worker.consumer import Worker
from worker.settings import WorkerSettings


@pytest.fixture
async def db(db_engine):
    """Session factory on the test schema; tables emptied after each test."""
    yield make_sessionmaker(db_engine)
    async with db_engine.begin() as conn:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def settings(make_topic) -> WorkerSettings:
    """Worker settings on throwaway topics, a fresh consumer group and Redis key prefix."""
    settings = WorkerSettings(
        _env_file=None,
        pr_events_topic=make_topic(3),
        pr_events_dlq_topic=make_topic(1),
        consumer_group=f"test-worker-{uuid.uuid4().hex[:8]}",
        poll_timeout=0.2,
        backoff_base=0.01,
        backoff_max=0.05,
        redis_key_prefix=f"test-{uuid.uuid4().hex[:8]}:",
        processing_lease_seconds=2,
    )
    yield settings
    client = redis.Redis.from_url(settings.redis_url)
    keys = list(client.scan_iter(f"{settings.redis_key_prefix}*"))
    if keys:
        client.delete(*keys)
    client.close()


def _make_envelope(i: int = 0, *, repo: str = "octo/sandbox") -> EventEnvelope:
    return EventEnvelope(
        delivery_id=f"d-{i}",
        type="pull_request.opened",
        payload={"action": "opened", "number": i, "repository": {"full_name": repo}},
    )


@pytest.fixture
def make_envelope():
    return _make_envelope


@pytest.fixture
def produce(settings) -> Callable[..., list[EventEnvelope]]:
    """Publish envelopes (or raw bytes) to the test pr-events topic and wait for the acks."""

    def _produce(*items: EventEnvelope | bytes, key: str = "octo/sandbox") -> list:
        producer = Producer(settings.kafka_bootstrap_servers)
        for item in items:
            value = item.to_bytes() if isinstance(item, EventEnvelope) else item
            producer.publish(settings.pr_events_topic, key, value)
        assert all(r.ok for r in producer.flush_results(10))
        return list(items)

    return _produce


async def _wait_until(check: Callable[[], Awaitable[bool]], seconds: float = 20.0) -> None:
    try:
        async with asyncio.timeout(seconds):
            while not await check():  # noqa: ASYNC110 (polling the database)
                await asyncio.sleep(0.1)
    except TimeoutError:
        raise AssertionError(f"condition not met within {seconds}s") from None


@pytest.fixture
def processed_count(db) -> Callable[[], Awaitable[int]]:
    async def _count() -> int:
        async with db() as session:
            return await session.scalar(select(func.count()).select_from(ProcessedEvent))

    return _count


@pytest.fixture
def wait_for_processed(processed_count) -> Callable[[int], Awaitable[None]]:
    """`await wait_for_processed(n)`: until processed_events holds n rows (or fail)."""

    async def _wait(n: int, seconds: float = 20.0) -> None:
        async def check() -> bool:
            return await processed_count() == n

        await _wait_until(check, seconds)

    return _wait


@pytest.fixture
def run_worker(settings, db):
    """Start a Worker task; the fixture stops every started worker at the end of the test."""
    started: list[tuple[Worker, asyncio.Task]] = []

    def _run(**kwargs) -> tuple[Worker, asyncio.Task]:
        worker = Worker(settings, db, **kwargs)
        task = asyncio.create_task(worker.run())
        started.append((worker, task))
        return worker, task

    yield _run
    for worker, _ in started:
        worker.stop()
    for _, task in started:
        if not task.done():
            # Reaped by the event loop; errors surface in the test that awaited it.
            task.cancel()
