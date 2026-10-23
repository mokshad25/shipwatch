"""Outbox relay: publishes committed outbox rows to Kafka in order, at least once (SPEC_W2)."""

import asyncio
import contextlib
from typing import Protocol

from shipwatch_common.kafka import DeliveryResult, EventEnvelope
from shipwatch_common.logging import get_logger
from shipwatch_common.models import Outbox
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = get_logger(__name__)

# Arbitrary constant naming the Postgres advisory lock that only one relay may hold at a time.
RELAY_LOCK_ID = 0x5348_4950  # "SHIP"
MAX_ERROR_LEN = 1000


class ProducerLike(Protocol):
    def publish(self, topic: str, key: str, value: bytes) -> None: ...
    def flush_results(self, timeout: float = ...) -> list[DeliveryResult]: ...


class OutboxRelay:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        producer: ProducerLike,
        *,
        batch_size: int = 100,
        poll_interval: float = 0.5,
        max_backoff: float = 10.0,
        flush_timeout: float = 15.0,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._producer = producer
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._max_backoff = max_backoff
        self._flush_timeout = flush_timeout
        self._wake = asyncio.Event()
        self._stopping = False

    def wake(self) -> None:
        """Ask the loop to run now (called after a webhook commits an outbox row)."""
        self._wake.set()

    async def run_once(self) -> tuple[int, bool]:
        """Publish one batch. Returns (rows marked sent, whether any row failed)."""
        async with self._sessionmaker.begin() as session:
            # Only one relay publishes at a time, otherwise two could interleave a repo's events.
            if not await session.scalar(select(func.pg_try_advisory_xact_lock(RELAY_LOCK_ID))):
                return 0, False
            rows = (
                await session.scalars(
                    select(Outbox)
                    .where(Outbox.sent_at.is_(None))
                    .order_by(Outbox.id)
                    .limit(self._batch_size)
                )
            ).all()
            if not rows:
                return 0, False

            for row in rows:
                envelope = EventEnvelope.model_validate(row.envelope)
                self._producer.publish(row.topic, row.key, envelope.to_bytes())
            results = await asyncio.to_thread(self._producer.flush_results, self._flush_timeout)

            # Mark only the prefix before the first failure: the failed row and everything after
            # it are retried together next round, so a later event never overtakes an earlier one.
            sent = 0
            for row, result in zip(rows, results, strict=True):
                if not result.ok:
                    row.attempts += 1
                    row.last_error = (result.error or "")[:MAX_ERROR_LEN]
                    log.warning(
                        "outbox publish failed",
                        event_id=str(row.event_id),
                        attempts=row.attempts,
                        error=row.last_error,
                    )
                    return sent, True
                row.sent_at = func.now()
                sent += 1
            return sent, False

    async def run(self) -> None:
        failures = 0
        while not self._stopping:
            self._wake.clear()
            try:
                sent, failed = await self.run_once()
            except Exception:
                log.exception("outbox relay error")
                sent, failed = 0, True
            if sent:
                log.info("outbox published", count=sent)
            failures = failures + 1 if failed else 0
            if sent == self._batch_size and not failed:
                continue  # more may be waiting
            delay = (
                min(self._poll_interval * 2**failures, self._max_backoff)
                if failures
                else self._poll_interval
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), delay)

    def stop(self) -> None:
        self._stopping = True
        self._wake.set()
