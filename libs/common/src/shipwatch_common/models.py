from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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
