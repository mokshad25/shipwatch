"""Kafka helpers shared by services: the event envelope, a typed producer and topic setup.

confluent-kafka is synchronous; callers in async code run `flush_results` in a thread.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from confluent_kafka import KafkaError, KafkaException, Message
from confluent_kafka import Producer as _Producer
from confluent_kafka.admin import AdminClient, NewTopic
from pydantic import BaseModel, Field

# librdkafka's own log lines go through stdlib logging, so they come out as JSON like ours.
RDKAFKA_LOGGER = logging.getLogger("rdkafka")


def _now() -> datetime:
    return datetime.now(UTC)


class EventEnvelope(BaseModel):
    """What every message on `pr-events` carries (docs/SPEC_W2.md)."""

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    delivery_id: str
    type: str
    occurred_at: datetime = Field(default_factory=_now)
    payload: dict[str, Any]

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> "EventEnvelope":
        return cls.model_validate_json(data)


@dataclass(frozen=True)
class DeliveryResult:
    """Outcome of one produced message, in the order `publish` was called."""

    error: str | None = None
    partition: int | None = None
    offset: int | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Producer:
    """Thin wrapper: idempotent, acks=all, and delivery reports collected per publish call."""

    def __init__(self, bootstrap_servers: str, **config: Any) -> None:
        self._producer = _Producer(
            {
                "bootstrap.servers": bootstrap_servers,
                "logger": RDKAFKA_LOGGER,
                "acks": "all",
                "enable.idempotence": True,
                "linger.ms": 5,
                # Give up on a message after this long; the caller decides what to do with it.
                "delivery.timeout.ms": 10_000,
                **config,
            }
        )
        self._results: list[DeliveryResult | None] = []

    def publish(
        self,
        topic: str,
        key: str | bytes | None,
        value: bytes,
        headers: dict[str, str | bytes] | None = None,
    ) -> None:
        index = len(self._results)
        self._results.append(None)

        def on_delivery(err: KafkaError | None, msg: Message) -> None:
            self._results[index] = (
                DeliveryResult(error=str(err))
                if err is not None
                else DeliveryResult(partition=msg.partition(), offset=msg.offset())
            )

        try:
            self._producer.produce(
                topic, key=key, value=value, headers=headers, on_delivery=on_delivery
            )
        except (BufferError, KafkaException) as exc:  # local queue full, invalid topic, ...
            self._results[index] = DeliveryResult(error=repr(exc))

    def publish_envelope(self, topic: str, key: str, envelope: EventEnvelope) -> None:
        self.publish(topic, key, envelope.to_bytes())

    def poll(self, timeout: float = 0) -> None:
        """Serve delivery callbacks without waiting for everything (for long publish loops)."""
        self._producer.poll(timeout)

    def flush_results(self, timeout: float = 15.0) -> list[DeliveryResult]:
        """Wait for every pending publish and return their results in publish order.

        Messages still undelivered when `timeout` expires are reported as failed (they may still
        arrive later, which is fine for at-least-once callers).
        """
        self._producer.flush(timeout)
        results = [r or DeliveryResult(error="flush timed out") for r in self._results]
        self._results = []
        return results

    def close(self, timeout: float = 5.0) -> None:
        self._producer.flush(timeout)


def ensure_topics(bootstrap_servers: str, partitions: dict[str, int]) -> None:
    """Create the given topics (name -> partition count) if they don't exist yet."""
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    existing = set(admin.list_topics(timeout=10).topics)
    new = [NewTopic(t, num_partitions=n) for t, n in partitions.items() if t not in existing]
    for future in admin.create_topics(new).values() if new else []:
        try:
            future.result(timeout=10)
        except KafkaException as exc:  # created concurrently by someone else: fine
            if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                raise


def delete_topics(bootstrap_servers: str, topics: list[str]) -> None:
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    for future in admin.delete_topics(topics).values():
        future.result(timeout=10)
