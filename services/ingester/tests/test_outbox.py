import asyncio

from ingester.relay import RELAY_LOCK_ID, OutboxRelay
from shipwatch_common.kafka import DeliveryResult, EventEnvelope, Producer
from shipwatch_common.models import Outbox
from shipwatch_common.settings import get_settings
from sqlalchemy import func, select


class FakeProducer:
    """Records publishes; `fail_at` makes the n-th message of a flush fail."""

    def __init__(self, fail_at: int | None = None) -> None:
        self.fail_at = fail_at
        self.pending: list[tuple[str, str, bytes]] = []
        self.published: list[tuple[str, str, bytes]] = []

    def publish(self, topic: str, key: str, value: bytes) -> None:
        self.pending.append((topic, key, value))

    def flush_results(self, timeout: float = 0) -> list[DeliveryResult]:
        results = []
        for i, msg in enumerate(self.pending):
            if i == self.fail_at:
                results.append(DeliveryResult(error="broker down"))
            else:
                self.published.append(msg)
                results.append(DeliveryResult(partition=0, offset=len(self.published)))
        self.pending = []
        return results


async def outbox_rows(db) -> list[Outbox]:
    async with db() as session:
        return list((await session.scalars(select(Outbox).order_by(Outbox.id))).all())


async def test_pull_request_delivery_writes_one_outbox_row(db, send, pr_payload):
    resp = await send(pr_payload(action="opened"), delivery="d-1")

    assert resp.status_code == 202
    [row] = await outbox_rows(db)
    envelope = EventEnvelope.model_validate(row.envelope)
    assert (row.topic, row.key, row.sent_at) == ("pr-events", "octo/sandbox", None)
    assert row.event_id == envelope.event_id
    assert (envelope.delivery_id, envelope.type) == ("d-1", "pull_request.opened")
    assert envelope.payload == pr_payload(action="opened")


async def test_duplicate_and_ignored_deliveries_write_no_outbox_row(db, send, pr_payload):
    await send(pr_payload(), delivery="d-1")
    await send(pr_payload(), delivery="d-1")
    await send({"zen": "hi"}, event="ping")

    assert len(await outbox_rows(db)) == 1


async def test_outbox_row_rolls_back_with_the_delivery(db, send, pr_payload):
    # A payload that fails inside the transaction (missing PR fields) must leave no outbox row.
    bad = pr_payload()
    del bad["pull_request"]["head"]
    resp = await send(bad)

    assert resp.status_code == 500
    assert await outbox_rows(db) == []


async def test_relay_publishes_in_order_and_marks_sent(db, send, pr_payload):
    for i in range(3):
        await send(pr_payload(), delivery=f"d-{i}")
    producer = FakeProducer()

    assert await OutboxRelay(db, producer).run_once() == (3, False)
    rows = await outbox_rows(db)
    assert all(r.sent_at is not None for r in rows)
    delivered = [EventEnvelope.from_bytes(v).delivery_id for _, _, v in producer.published]
    assert delivered == ["d-0", "d-1", "d-2"]
    assert {k for _, k, _ in producer.published} == {"octo/sandbox"}
    # Nothing left: the next round publishes nothing.
    assert await OutboxRelay(db, producer).run_once() == (0, False)


async def test_failure_keeps_failed_row_and_everything_after_it(db, send, pr_payload):
    for i in range(4):
        await send(pr_payload(), delivery=f"d-{i}")

    assert await OutboxRelay(db, FakeProducer(fail_at=1)).run_once() == (1, True)
    rows = await outbox_rows(db)
    assert [r.sent_at is not None for r in rows] == [True, False, False, False]
    assert (rows[1].attempts, rows[1].last_error) == (1, "broker down")
    assert rows[2].attempts == 0

    # Next round retries from the failed row, in order.
    producer = FakeProducer()
    assert await OutboxRelay(db, producer).run_once() == (3, False)
    delivered = [EventEnvelope.from_bytes(v).delivery_id for _, _, v in producer.published]
    assert delivered == ["d-1", "d-2", "d-3"]


async def test_relay_skips_while_another_relay_holds_the_lock(db, send, pr_payload):
    await send(pr_payload())
    producer = FakeProducer()
    async with db.begin() as other:
        await other.scalar(select(func.pg_advisory_xact_lock(RELAY_LOCK_ID)))
        assert await OutboxRelay(db, producer).run_once() == (0, False)
    assert producer.published == []
    assert await OutboxRelay(db, producer).run_once() == (1, False)


async def test_relay_loop_publishes_after_wake_and_stops(db, send, pr_payload):
    producer = FakeProducer()
    relay = OutboxRelay(db, producer, poll_interval=30)
    task = asyncio.create_task(relay.run())
    await asyncio.sleep(0.05)

    await send(pr_payload())
    relay.wake()
    for _ in range(100):
        if producer.published:
            break
        await asyncio.sleep(0.02)
    relay.stop()
    await asyncio.wait_for(task, 5)

    assert len(producer.published) == 1


async def test_relay_publishes_to_redpanda_keyed_by_repo(
    db, send, pr_payload, kafka_topic, consume_all, monkeypatch
):
    monkeypatch.setattr(get_settings(), "pr_events_topic", kafka_topic)
    await send(pr_payload(action="opened"), delivery="d-real")
    producer = Producer(get_settings().kafka_bootstrap_servers)

    assert await OutboxRelay(db, producer).run_once() == (1, False)
    [msg] = await asyncio.to_thread(consume_all, kafka_topic, 1)
    assert msg.key() == b"octo/sandbox"
    envelope = EventEnvelope.from_bytes(msg.value())
    assert (envelope.delivery_id, envelope.type) == ("d-real", "pull_request.opened")


async def test_unreachable_kafka_leaves_rows_unsent(db, send, pr_payload):
    await send(pr_payload())
    producer = Producer("localhost:1", **{"delivery.timeout.ms": 500, "socket.timeout.ms": 200})

    sent, failed = await OutboxRelay(db, producer, flush_timeout=3).run_once()

    assert (sent, failed) == (0, True)
    [row] = await outbox_rows(db)
    assert (row.sent_at, row.attempts) == (None, 1)
