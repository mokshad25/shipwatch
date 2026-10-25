"""Recording that an event was processed. The PR risk agent itself is in worker.verdicts."""

from dataclasses import dataclass

from shipwatch_common.kafka import EventEnvelope
from shipwatch_common.logging import get_logger
from shipwatch_common.models import ProcessedEvent
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = get_logger(__name__)


@dataclass(frozen=True)
class Source:
    """Where a message came from (kept with the result for tracing), and which attempt this is."""

    topic: str
    partition: int
    offset: int
    attempt: int = 1
    max_attempts: int = 1

    @property
    def can_retry(self) -> bool:
        return self.attempt < self.max_attempts


async def record_processed(session: AsyncSession, envelope: EventEnvelope, source: Source) -> None:
    """Insert the processed_events row inside the caller's transaction."""
    stmt = insert(ProcessedEvent).values(
        event_id=envelope.event_id,
        delivery_id=envelope.delivery_id,
        type=envelope.type,
        topic=source.topic,
        partition=source.partition,
        offset=source.offset,
    )
    # The primary key is the last line of defence: a redelivery that got past Redis (e.g. the
    # worker died after this commit but before marking the key done) only bumps `deliveries`.
    stmt = stmt.on_conflict_do_update(
        index_elements=[ProcessedEvent.event_id],
        set_={"deliveries": ProcessedEvent.deliveries + 1},
    ).returning(ProcessedEvent.deliveries)
    deliveries = await session.scalar(stmt)
    if deliveries == 1:
        log.info("event processed", type=envelope.type)
    else:
        log.warning("event already processed (caught by database)", deliveries=deliveries)


async def process(
    sessionmaker: async_sessionmaker[AsyncSession], envelope: EventEnvelope, source: Source
) -> None:
    """Default process_fn: only record the event (used when no agent pipeline is wired in)."""
    async with sessionmaker.begin() as session:
        await record_processed(session, envelope, source)
