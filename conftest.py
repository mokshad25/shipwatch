"""Fixtures shared by every package's tests: a Postgres schema and Kafka topics on compose."""

import time
import uuid
from collections.abc import Callable

import pytest
from confluent_kafka import Consumer, Message
from shipwatch_common.db import make_engine
from shipwatch_common.kafka import delete_topics, ensure_topics
from shipwatch_common.models import Base
from shipwatch_common.settings import ServiceSettings
from sqlalchemy import text

KAFKA = ServiceSettings().kafka_bootstrap_servers


@pytest.fixture(scope="session")
async def db_engine():
    """Engine bound to a fresh schema in the real Postgres; the schema is dropped afterwards."""
    url = ServiceSettings().database_url
    schema = f"test_{uuid.uuid4().hex[:12]}"
    admin = make_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = make_engine(url, connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine

    await engine.dispose()
    async with admin.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin.dispose()


@pytest.fixture
def make_topic():
    """Create throwaway topics (`make_topic(partitions)`); all deleted after the test."""
    created: list[str] = []

    def _make(partitions: int = 1) -> str:
        topic = f"test-{uuid.uuid4().hex[:12]}"
        ensure_topics(KAFKA, {topic: partitions})
        created.append(topic)
        return topic

    yield _make
    if created:
        delete_topics(KAFKA, created)


@pytest.fixture
def kafka_topic(make_topic) -> str:
    return make_topic()


def _consume_all(topic: str, expected: int, timeout: float = 15.0) -> list[Message]:
    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA,
            "group.id": f"test-{uuid.uuid4().hex}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topic])
    messages: list[Message] = []
    deadline = time.monotonic() + timeout
    try:
        while len(messages) < expected and time.monotonic() < deadline:
            msg = consumer.poll(0.5)
            if msg is not None and msg.error() is None:
                messages.append(msg)
        # Linger briefly so an unexpected extra message would be noticed too.
        extra = consumer.poll(0.5)
        if extra is not None and extra.error() is None:
            messages.append(extra)
    finally:
        consumer.close()
    assert len(messages) == expected, f"got {len(messages)} messages from {topic}, want {expected}"
    return messages


@pytest.fixture
def consume_all() -> Callable[..., list[Message]]:
    """`consume_all(topic, n)`: read exactly `n` messages from the start of `topic`."""
    return _consume_all
