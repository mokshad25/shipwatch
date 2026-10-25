import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, NUMERIC, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names so Alembic migrations can refer to them.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Repo(Base):
    __tablename__ = "repos"

    # GitHub's repository id (stable across renames), not a local sequence.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    full_name: Mapped[str] = mapped_column(String(255), index=True)


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repo_id", "number"),)

    # GitHub's pull request id.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    repo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("repos.id"))
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(32))
    head_sha: Mapped[str] = mapped_column(String(40))
    base_branch: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # GitHub's pull_request.updated_at; guards against older deliveries overwriting newer state.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # GitHub's X-GitHub-Delivery; the unique constraint is what makes ingestion idempotent.
    delivery_id: Mapped[str] = mapped_column(String(64), unique=True)
    event_type: Mapped[str] = mapped_column(String(64))
    action: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Outbox(Base):
    """Messages waiting to be published, written in the same transaction as their data."""

    __tablename__ = "outbox"
    # The relay only ever scans unsent rows, so index just those.
    __table_args__ = (Index("ix_outbox_unsent", "id", postgresql_where=text("sent_at IS NULL")),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID, unique=True)
    topic: Mapped[str] = mapped_column(String(255))
    key: Mapped[str] = mapped_column(String(255))
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)


class ProcessedEvent(Base):
    """One row per event the worker has processed (its placeholder side effect until week 3)."""

    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    delivery_id: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(128))
    topic: Mapped[str] = mapped_column(String(255))
    partition: Mapped[int] = mapped_column(Integer)
    offset: Mapped[int] = mapped_column(BigInteger)
    # How many times processing reached the database for this event; > 1 means a redelivery got
    # past the Redis check and was stopped here instead.
    deliveries: Mapped[int] = mapped_column(Integer, server_default="1")
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class VerdictRecord(Base):
    """The PR risk agent's verdict, one per (pr_id, head_sha) (docs/SPEC_W3.md, D7).

    The full tool-call trace lives in MongoDB under `trace_id`.
    """

    __tablename__ = "verdicts"
    __table_args__ = (UniqueConstraint("pr_id", "head_sha"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    pr_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pull_requests.id"))
    head_sha: Mapped[str] = mapped_column(String(40))
    level: Mapped[str] = mapped_column(String(16))  # low | medium | high | needs_review
    score: Mapped[int | None] = mapped_column(Integer)  # NULL when level is needs_review
    reason: Mapped[str | None] = mapped_column(Text)
    assessment: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(NUMERIC(10, 6), server_default="0")
    trace_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
