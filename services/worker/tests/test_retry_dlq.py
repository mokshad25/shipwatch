import asyncio
import json
import random

import pytest
from confluent_kafka import Consumer, TopicPartition
from pydantic import ValidationError
from shipwatch_common.kafka import DeliveryResult, EventEnvelope
from sqlalchemy.exc import OperationalError
from worker.consumer import DeadLetterError
from worker.dlq import original_value
from worker.processing import process
from worker.retry import InjectedFailure, backoff_delay, is_retryable


def failing(times: int, exc: Exception, *, only: str | None = None):
    """A process_fn that raises `exc` for its first `times` calls (only for delivery `only`, if
    given), then processes normally. `calls` counts attempts on the failing message."""
    calls = {"n": 0}

    async def _process(sessionmaker, envelope, source):
        if only is None or envelope.delivery_id == only:
            calls["n"] += 1
            if calls["n"] <= times:
                raise exc
        await process(sessionmaker, envelope, source)

    _process.calls = calls
    return _process


def committed_total(settings) -> int:
    consumer = Consumer(
        {"bootstrap.servers": settings.kafka_bootstrap_servers, "group.id": settings.consumer_group}
    )
    try:
        parts = [TopicPartition(settings.pr_events_topic, p) for p in range(3)]
        return sum(max(tp.offset, 0) for tp in consumer.committed(parts, timeout=10))
    finally:
        consumer.close()


@pytest.mark.parametrize(
    ("exc", "retryable"),
    [
        (TimeoutError(), True),
        (ConnectionResetError(), True),
        (InjectedFailure(), True),
        (OperationalError("select 1", {}, Exception("server closed the connection")), True),
        (RuntimeError("unexpected"), True),
        (json.JSONDecodeError("bad", "x", 0), False),
    ],
)
def test_error_classification(exc, retryable):
    assert is_retryable(exc) is retryable


def test_validation_error_is_not_retryable():
    with pytest.raises(ValidationError) as info:
        EventEnvelope.from_bytes(b"{}")
    assert not is_retryable(info.value)


def test_backoff_grows_exponentially_with_full_jitter_and_a_cap():
    rng = random.Random(0)
    for attempt, ceiling in [(1, 0.5), (2, 1.0), (3, 2.0), (4, 4.0), (5, 5.0), (10, 5.0)]:
        delays = [backoff_delay(attempt, 0.5, 5.0, rng) for _ in range(200)]
        assert all(0 <= d <= ceiling for d in delays)
        # Full jitter spreads over the whole range, not just near the ceiling.
        assert max(delays) > ceiling * 0.8
        assert min(delays) < ceiling * 0.2


async def test_transient_failure_is_retried_then_succeeds(
    produce, run_worker, settings, make_envelope, wait_for_processed, consume_all
):
    process_fn = failing(2, ConnectionResetError("db blip"))
    produce(make_envelope(1))
    worker, task = run_worker(process_fn=process_fn)

    await wait_for_processed(1)
    worker.stop()
    await asyncio.wait_for(task, 10)

    assert process_fn.calls["n"] == 3
    await asyncio.to_thread(consume_all, settings.pr_events_dlq_topic, 0, 1.0)


async def test_exhausted_retries_go_to_the_dlq_and_are_committed(
    produce, run_worker, settings, make_envelope, wait_for_processed, consume_all
):
    process_fn = failing(99, TimeoutError("github slow"), only="d-1")
    [poison] = produce(make_envelope(1))
    produce(make_envelope(2))  # same key, behind the poison message: must not be stuck
    worker, task = run_worker(process_fn=process_fn)

    await wait_for_processed(1)
    [dead] = await asyncio.to_thread(consume_all, settings.pr_events_dlq_topic, 1)
    worker.stop()
    await asyncio.wait_for(task, 10)

    record = json.loads(dead.value())
    assert dead.key() == b"octo/sandbox"
    assert process_fn.calls["n"] == 3
    assert record["error"]["type"] == "TimeoutError"
    assert record["error"]["message"] == "github slow"
    assert record["error"]["attempts"] == 3
    assert record["error"]["retryable"] is True
    assert record["error"]["first_failed_at"] <= record["error"]["last_failed_at"]
    assert record["original"]["topic"] == settings.pr_events_topic
    assert EventEnvelope.from_bytes(original_value(record)) == poison
    assert committed_total(settings) == 2


async def test_invalid_message_goes_to_the_dlq_without_retries(
    produce, run_worker, settings, consume_all
):
    produce(b"{not json", b'{"event_id": "not-a-uuid"}')
    worker, task = run_worker()

    dead = await asyncio.to_thread(consume_all, settings.pr_events_dlq_topic, 2)
    worker.stop()
    await asyncio.wait_for(task, 10)

    records = [json.loads(m.value()) for m in dead]
    assert [r["error"]["attempts"] for r in records] == [1, 1]
    assert [r["error"]["retryable"] for r in records] == [False, False]
    assert {r["error"]["type"] for r in records} == {"ValidationError"}
    # Bytes that aren't JSON survive exactly, base64-encoded.
    assert records[0]["original"]["value_encoding"] == "base64"
    assert original_value(records[0]) == b"{not json"
    assert committed_total(settings) == 2


async def test_fail_rate_one_sends_everything_to_the_dlq(
    produce, run_worker, settings, make_envelope, consume_all, processed_count
):
    settings.fail_rate = 1.0
    produce(make_envelope(1))
    worker, task = run_worker()

    [dead] = await asyncio.to_thread(consume_all, settings.pr_events_dlq_topic, 1)
    worker.stop()
    await asyncio.wait_for(task, 10)

    assert json.loads(dead.value())["error"]["type"] == "InjectedFailure"
    assert await processed_count() == 0


class BrokenProducer:
    def publish(self, topic, key, value, headers=None):
        pass

    def flush_results(self, timeout=0):
        return [DeliveryResult(error="dlq unavailable")]


async def test_failed_dlq_publish_does_not_commit(produce, run_worker, settings, make_envelope):
    produce(make_envelope(1))
    worker, task = run_worker(process_fn=failing(99, TimeoutError()), dlq_producer=BrokenProducer())

    with pytest.raises(DeadLetterError):
        await asyncio.wait_for(task, 20)
    assert committed_total(settings) == 0  # redelivered after restart, not lost


async def test_rate_limit_retry_after_is_honoured_between_attempts(
    produce, run_worker, make_envelope, wait_for_processed
):
    from agent.service import AgentRateLimited

    times: list[float] = []
    inner = failing(1, AgentRateLimited("429", retry_after=0.8))

    async def _process(sessionmaker, envelope, source):
        times.append(asyncio.get_running_loop().time())
        await inner(sessionmaker, envelope, source)

    produce(make_envelope(1))
    run_worker(process_fn=_process)  # test settings: backoff at most 0.05 s

    await wait_for_processed(1)

    assert len(times) == 2
    assert times[1] - times[0] >= 0.8
