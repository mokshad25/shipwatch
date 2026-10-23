"""The consume loop: poll, process, commit the offset only after success (docs/SPEC_W2.md)."""

import asyncio
import json
import random
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from confluent_kafka import Consumer, Message
from redis.asyncio import Redis
from redis.exceptions import RedisError
from shipwatch_common.kafka import RDKAFKA_LOGGER, DeliveryResult, EventEnvelope, Producer
from shipwatch_common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from worker.dlq import dead_letter_record
from worker.idempotency import Claim, EventGate
from worker.processing import Source, process
from worker.retry import InjectedFailure, backoff_delay, is_retryable
from worker.settings import WorkerSettings

log = get_logger(__name__)

ProcessFn = Callable[[async_sessionmaker[AsyncSession], EventEnvelope, Source], Awaitable[None]]


class ProducerLike(Protocol):
    def publish(
        self, topic: str, key: bytes | None, value: bytes, headers: dict | None = None
    ) -> None: ...
    def flush_results(self, timeout: float = ...) -> list[DeliveryResult]: ...


class DeadLetterError(Exception):
    """The DLQ publish failed. The offset is not committed, so the message will be redelivered."""


class Worker:
    def __init__(
        self,
        settings: WorkerSettings,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        process_fn: ProcessFn = process,
        dlq_producer: ProducerLike | None = None,
        gate: EventGate | None = None,
        consumer_config: dict[str, Any] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.process_fn = process_fn
        self.dlq_producer = dlq_producer or Producer(settings.kafka_bootstrap_servers)
        self.rng = rng or random.Random()
        self.gate = gate or EventGate(
            Redis.from_url(settings.redis_url),
            prefix=settings.redis_key_prefix,
            lease_seconds=settings.processing_lease_seconds,
            done_ttl_seconds=settings.done_ttl_seconds,
        )
        self._consumer = Consumer(
            {
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "logger": RDKAFKA_LOGGER,
                "group.id": settings.consumer_group,
                # We commit each offset ourselves, after the message is fully handled.
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
                # How long a dead (e.g. SIGKILLed) member keeps its partitions before a rebalance.
                "session.timeout.ms": settings.session_timeout_ms,
                **(
                    {"group.instance.id": settings.consumer_instance_id}
                    if settings.consumer_instance_id
                    else {}
                ),
                **(consumer_config or {}),
            }
        )
        self._stopping = False

    def stop(self) -> None:
        """Finish the message in hand, commit it, then leave the loop (SIGTERM/SIGINT)."""
        if not self._stopping:
            log.info("worker stopping")
        self._stopping = True

    async def run(self) -> None:
        self._consumer.subscribe([self.settings.pr_events_topic])
        log.info("worker started", topic=self.settings.pr_events_topic)
        try:
            while not self._stopping:
                msg = await asyncio.to_thread(self._consumer.poll, self.settings.poll_timeout)
                if msg is None:
                    continue
                if msg.error() is not None:
                    log.warning("consumer error", error=str(msg.error()))
                    continue
                await self.handle(msg)
                # Synchronous commit of exactly this message: once it returns, a restart resumes
                # after it. A crash before this line redelivers the message (at-least-once).
                await asyncio.to_thread(self._consumer.commit, message=msg, asynchronous=False)
        finally:
            await asyncio.to_thread(self._consumer.close)
            await self.gate.redis.aclose()
            log.info("worker stopped")

    async def handle(self, msg: Message) -> None:
        """Process `msg`, retrying transient errors; give up into the DLQ. Returns once the message
        is safe to commit (processed or dead-lettered); raises DeadLetterError otherwise."""
        source = Source(msg.topic(), msg.partition(), msg.offset())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            topic=source.topic, partition=source.partition, offset=source.offset
        )
        first_failed_at: datetime | None = None
        attempt = 0
        while True:
            attempt += 1
            try:
                await self.attempt(msg, source)
                return
            except Exception as exc:
                now = datetime.now(UTC)
                first_failed_at = first_failed_at or now
                retryable = is_retryable(exc)
                log.warning(
                    "processing failed",
                    attempt=attempt,
                    retryable=retryable,
                    error_type=type(exc).__name__,
                    error=str(exc)[:500],
                )
                if not retryable or attempt >= self.settings.max_attempts:
                    await self.dead_letter(
                        msg,
                        exc,
                        attempts=attempt,
                        retryable=retryable,
                        first_failed_at=first_failed_at,
                        last_failed_at=now,
                    )
                    return
                delay = backoff_delay(
                    attempt, self.settings.backoff_base, self.settings.backoff_max, self.rng
                )
                await asyncio.sleep(delay)

    async def attempt(self, msg: Message, source: Source) -> None:
        envelope = EventEnvelope.from_bytes(msg.value())
        structlog.contextvars.bind_contextvars(
            event_id=str(envelope.event_id), delivery_id=envelope.delivery_id
        )
        claim, token = await self.claim(envelope)
        if claim is Claim.DONE:
            log.info("event already processed, skipping")
            return
        # Keep renewing the lease while we work, so a slow attempt never loses it to a redelivery.
        keeper = (
            asyncio.create_task(self._keep_lease(envelope.event_id, token))
            if token is not None
            else None
        )
        try:
            if self.settings.fail_rate and self.rng.random() < self.settings.fail_rate:
                raise InjectedFailure(f"injected failure (FAIL_RATE={self.settings.fail_rate})")
            await self.process_fn(self.sessionmaker, envelope, source)
        except BaseException:
            if token is not None:
                await self._best_effort("release", self.gate.release(envelope.event_id, token))
            raise
        finally:
            if keeper is not None:
                keeper.cancel()
        await self._best_effort("mark done", self.gate.mark_done(envelope.event_id))

    async def claim(self, envelope: EventEnvelope) -> tuple[Claim, str | None]:
        """Claim the event in Redis, waiting out a lease left by an earlier (crashed) attempt.

        If Redis is unavailable we process anyway: the processed_events primary key still prevents
        a duplicate effect, and an outage shouldn't push every event into the DLQ.
        """
        try:
            while True:
                claim, token = await self.gate.claim(envelope.event_id)
                if claim is not Claim.IN_PROGRESS:
                    return claim, token
                remaining = await self.gate.lease_remaining(envelope.event_id)
                log.info("event in progress elsewhere, waiting for lease", seconds=remaining)
                await asyncio.sleep(min(max(remaining, 0.05), 1.0))
        except RedisError as exc:
            log.warning("redis unavailable, processing without dedupe gate", error=str(exc))
            return Claim.CLAIMED, None

    async def _keep_lease(self, event_id: uuid.UUID, token: str) -> None:
        interval = self.gate.lease_seconds / 3
        while True:
            await asyncio.sleep(interval)
            try:
                if not await self.gate.extend(event_id, token):
                    log.warning("processing lease lost; a redelivery may run concurrently")
                    return
            except RedisError as exc:
                log.warning("redis lease renewal failed", error=str(exc))

    async def _best_effort(self, what: str, op: Awaitable[Any]) -> None:
        try:
            await op
        except RedisError as exc:
            log.warning(f"redis {what} failed", error=str(exc))

    async def dead_letter(self, msg: Message, exc: BaseException, **info: Any) -> None:
        record = dead_letter_record(msg, exc, **info)
        self.dlq_producer.publish(
            self.settings.pr_events_dlq_topic, msg.key(), json.dumps(record).encode()
        )
        [result] = await asyncio.to_thread(self.dlq_producer.flush_results, 10.0)
        if not result.ok:
            # Don't commit: crash and let the message be redelivered rather than lose it.
            raise DeadLetterError(result.error)
        log.error("event dead-lettered", error_type=type(exc).__name__, attempts=info["attempts"])
