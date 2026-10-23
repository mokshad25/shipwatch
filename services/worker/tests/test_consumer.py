import asyncio
import os
import signal

from confluent_kafka import Consumer, TopicPartition
from shipwatch_common.models import ProcessedEvent
from sqlalchemy import select


def committed_offsets(settings) -> dict[int, int]:
    consumer = Consumer(
        {"bootstrap.servers": settings.kafka_bootstrap_servers, "group.id": settings.consumer_group}
    )
    try:
        parts = [TopicPartition(settings.pr_events_topic, p) for p in range(3)]
        return {tp.partition: tp.offset for tp in consumer.committed(parts, timeout=10)}
    finally:
        consumer.close()


async def test_five_events_make_five_processed_rows(
    db, produce, run_worker, settings, make_envelope, wait_for_processed
):
    envelopes = produce(*(make_envelope(i, repo=f"octo/r{i}") for i in range(5)))
    worker, task = run_worker()

    await wait_for_processed(5)
    worker.stop()
    await asyncio.wait_for(task, 10)

    async with db() as session:
        rows = (await session.scalars(select(ProcessedEvent))).all()
    assert {r.event_id for r in rows} == {e.event_id for e in envelopes}
    assert all(r.topic == settings.pr_events_topic for r in rows)
    # Every message's offset was committed: the group resumes after them.
    assert sum(o for o in committed_offsets(settings).values() if o > 0) == 5


async def test_stop_finishes_and_commits_the_message_in_hand(
    produce, run_worker, settings, make_envelope, processed_count
):
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_process(sessionmaker, envelope, source):
        started.set()
        await release.wait()
        from worker.processing import process

        await process(sessionmaker, envelope, source)

    produce(make_envelope(1))
    worker, task = run_worker(process_fn=slow_process)
    await asyncio.wait_for(started.wait(), 20)

    worker.stop()  # arrives mid-message
    release.set()
    await asyncio.wait_for(task, 10)

    assert await processed_count() == 1
    assert max(committed_offsets(settings).values()) == 1


async def test_sigterm_handler_stops_the_worker(db, run_worker):
    worker, task = run_worker()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, worker.stop)
    try:
        await asyncio.sleep(0.3)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(task, 10)
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
    assert task.done() and task.exception() is None


async def test_restart_resumes_after_committed_offsets(
    produce, run_worker, make_envelope, processed_count, wait_for_processed
):
    produce(make_envelope(1), make_envelope(2))
    worker, task = run_worker()
    await wait_for_processed(2)
    worker.stop()
    await asyncio.wait_for(task, 10)

    produce(make_envelope(3))
    worker, task = run_worker()  # same group
    await wait_for_processed(3)
    worker.stop()
    await asyncio.wait_for(task, 10)

    assert await processed_count() == 3


class AbandonedConsumer:
    """Wraps a consumer whose owner "crashed": close() never reaches the group coordinator."""

    def __init__(self, consumer):
        self._consumer = consumer

    def __getattr__(self, name):
        return getattr(self._consumer, name)

    def close(self):
        pass


async def test_static_member_takes_its_partitions_back_right_after_a_crash(
    produce, run_worker, settings, make_envelope, wait_for_processed
):
    # Long session timeout: with dynamic membership the restart would wait this long.
    settings.session_timeout_ms = 45_000
    settings.consumer_instance_id = "worker-test"
    produce(make_envelope(1))
    crashed, task = run_worker()
    crashed._consumer = AbandonedConsumer(crashed._consumer)
    await wait_for_processed(1)
    crashed.stop()
    await asyncio.wait_for(task, 10)

    produce(make_envelope(2))
    restarted, task = run_worker()
    await wait_for_processed(2, seconds=15)
    restarted.stop()
    await asyncio.wait_for(task, 10)
