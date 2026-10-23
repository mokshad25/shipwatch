import json
import uuid
from datetime import UTC, datetime

import pytest
from confluent_kafka.admin import AdminClient
from pydantic import ValidationError
from shipwatch_common.kafka import EventEnvelope, Producer, ensure_topics
from shipwatch_common.settings import ServiceSettings

KAFKA = ServiceSettings().kafka_bootstrap_servers


def test_envelope_round_trips_through_bytes():
    envelope = EventEnvelope(
        delivery_id="d-1", type="pull_request.opened", payload={"action": "opened", "n": [1, 2]}
    )

    assert EventEnvelope.from_bytes(envelope.to_bytes()) == envelope


def test_envelope_defaults_are_fresh_and_utc():
    a = EventEnvelope(delivery_id="d", type="t", payload={})
    b = EventEnvelope(delivery_id="d", type="t", payload={})

    assert a.event_id != b.event_id
    assert a.occurred_at.tzinfo is not None
    assert a.occurred_at <= datetime.now(UTC)


def test_envelope_wire_format_has_the_spec_fields():
    event_id = uuid.uuid4()
    envelope = EventEnvelope(event_id=event_id, delivery_id="d", type="t", payload={"x": 1})

    assert set(json.loads(envelope.to_bytes())) == {
        "event_id",
        "delivery_id",
        "type",
        "occurred_at",
        "payload",
    }
    assert json.loads(envelope.to_bytes())["event_id"] == str(event_id)


@pytest.mark.parametrize("raw", [b"not json", b'{"delivery_id": "d"}', b'{"event_id": "nope"}'])
def test_invalid_envelope_is_rejected(raw):
    with pytest.raises(ValidationError):
        EventEnvelope.from_bytes(raw)


def test_full_local_queue_is_reported_as_a_failed_delivery(kafka_topic):
    producer = Producer(KAFKA, **{"queue.buffering.max.messages": 1, "linger.ms": 1000})
    for i in range(3):
        producer.publish(kafka_topic, "k", f"m{i}".encode())

    results = producer.flush_results(10)

    assert results[0].ok
    assert [r.ok for r in results[1:]] == [False, False]
    assert "BufferError" in results[1].error


def test_ensure_topics_is_idempotent(make_topic):
    topic = make_topic(2)

    ensure_topics(KAFKA, {topic: 2})  # already exists: no error

    metadata = AdminClient({"bootstrap.servers": KAFKA}).list_topics(topic, timeout=10)
    assert len(metadata.topics[topic].partitions) == 2
