"""What the worker does with one event. For now: record it (the PR risk agent comes in week 3)."""

from dataclasses import dataclass

from shipwatch_common.kafka import EventEnvelope
from shipwatch_common.logging import get_logger
from shipwatch_common.models import ProcessedEvent
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = get_logger(__name__)


@dataclass(frozen=True)
class Source:
    """Where a message came from, kept with the result for tracing."""

    topic: str
    partition: int
    offset: int


async def process(
    sessionmaker: async_sessionmaker[AsyncSession], envelope: EventEnvelope, source: Source
) -> None:
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
    async with sessionmaker.begin() as session:
        deliveries = await session.scalar(stmt)
    if deliveries == 1:
        log.info("event processed", type=envelope.type)
    else:
        log.warning("event already processed (caught by database)", deliveries=deliveries)
