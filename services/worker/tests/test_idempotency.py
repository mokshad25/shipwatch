import asyncio
import json
import time

from redis.asyncio import Redis
from shipwatch_common.models import ProcessedEvent
from sqlalchemy import select
from worker.idempotency import Claim, EventGate
from worker.processing import process


def counting():
    calls: list[str] = []

    async def _process(sessionmaker, envelope, source):
        calls.append(envelope.delivery_id)
        await process(sessionmaker, envelope, source)

    _process.calls = calls
    return _process


async def stop(worker, task):
    worker.stop()
    await asyncio.wait_for(task, 10)


async def rows(db) -> list[ProcessedEvent]:
    async with db() as session:
        return list((await session.scalars(select(ProcessedEvent))).all())


def gate_for(settings, url=None) -> EventGate:
    return EventGate(
        Redis.from_url(url or settings.redis_url),
        prefix=settings.redis_key_prefix,
        lease_seconds=settings.processing_lease_seconds,
        done_ttl_seconds=60,
    )


async def test_same_event_delivered_twice_is_processed_once(
    db, produce, run_worker, make_envelope, wait_for_processed
):
    envelope = make_envelope(1)
    produce(envelope, envelope, make_envelope(2))  # duplicate, then a marker event
    process_fn = counting()
    worker, task = run_worker(process_fn=process_fn)

    await wait_for_processed(2)
    await asyncio.sleep(0.5)  # give a wrongly reprocessed duplicate time to show up
    await stop(worker, task)

    assert process_fn.calls == ["d-1", "d-2"]
    assert [r.deliveries for r in await rows(db)] == [1, 1]


async def test_done_key_survives_a_restart(
    db, produce, run_worker, make_envelope, wait_for_processed, settings
):
    envelope = make_envelope(1)
    produce(envelope)
    worker, task = run_worker()
    await wait_for_processed(1)
    await stop(worker, task)

    # Same event again, consumed by a different group (e.g. after an offset reset).
    settings.consumer_group += "-again"
    produce(envelope)
    process_fn = counting()
    worker, task = run_worker(process_fn=process_fn)
    await asyncio.sleep(2)
    await stop(worker, task)

    assert process_fn.calls == []


async def test_lease_left_by_a_crashed_attempt_expires_then_event_is_processed(
    db, produce, run_worker, make_envelope, wait_for_processed, settings
):
    envelope = make_envelope(1)
    gate = gate_for(settings)
    claim, _ = await gate.claim(envelope.event_id)  # "crashed" worker's lease, never released
    assert claim is Claim.CLAIMED
    produce(envelope)

    start = time.monotonic()
    worker, task = run_worker()
    await wait_for_processed(1)
    await stop(worker, task)
    await gate.redis.aclose()

    assert time.monotonic() - start >= settings.processing_lease_seconds * 0.5


async def test_crash_between_db_commit_and_done_is_caught_by_the_primary_key(
    db, produce, run_worker, make_envelope, wait_for_processed, settings
):
    envelope = make_envelope(1)
    produce(envelope)
    worker, task = run_worker()
    await wait_for_processed(1)
    await stop(worker, task)
    # As if the worker died after committing to Postgres but before `mark_done`.
    gate = gate_for(settings)
    await gate.redis.delete(gate.key(envelope.event_id))

    settings.consumer_group += "-again"
    worker, task = run_worker()
    for _ in range(100):
        if (await rows(db))[0].deliveries == 2:
            break
        await asyncio.sleep(0.1)
    await stop(worker, task)
    await gate.redis.aclose()

    [row] = await rows(db)
    assert row.deliveries == 2  # seen twice, applied once


async def test_dead_lettered_event_releases_its_key_for_replay(
    produce, run_worker, make_envelope, settings, consume_all
):
    settings.fail_rate = 1.0
    envelope = make_envelope(1)
    produce(envelope)
    worker, task = run_worker()
    [dead] = await asyncio.to_thread(consume_all, settings.pr_events_dlq_topic, 1)
    await stop(worker, task)

    assert json.loads(dead.value())["error"]["attempts"] == 3
    gate = gate_for(settings)
    assert await gate.redis.get(gate.key(envelope.event_id)) is None
    await gate.redis.aclose()


async def test_redis_down_still_processes_without_the_gate(
    db, produce, run_worker, make_envelope, wait_for_processed, settings
):
    produce(make_envelope(1))
    worker, task = run_worker(gate=gate_for(settings, "redis://localhost:1/0"))

    await wait_for_processed(1)
    await stop(worker, task)


async def test_lease_is_renewed_while_a_slow_attempt_runs(
    produce, run_worker, make_envelope, wait_for_processed, settings
):
    settings.processing_lease_seconds = 1
    envelope = make_envelope(1)
    gate = gate_for(settings)
    seen: list[Claim] = []

    async def slow_process(sessionmaker, env, source):
        await asyncio.sleep(2.5)  # well past the 1 s lease
        # A redelivered copy arriving now must still find the event in progress.
        seen.append((await gate.claim(env.event_id))[0])
        await process(sessionmaker, env, source)

    produce(envelope)
    worker, task = run_worker(process_fn=slow_process)
    await wait_for_processed(1)
    await stop(worker, task)

    assert seen == [Claim.IN_PROGRESS]
    assert await gate.redis.get(gate.key(envelope.event_id)) == b"done"
    await gate.redis.aclose()
