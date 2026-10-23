"""outbox table (transactional outbox for pr-events)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("event_id", postgresql.UUID(), nullable=False),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("envelope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
        sa.UniqueConstraint("event_id", name=op.f("uq_outbox_event_id")),
    )
    op.create_index(
        "ix_outbox_unsent", "outbox", ["id"], postgresql_where=sa.text("sent_at IS NULL")
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_unsent", table_name="outbox")
    op.drop_table("outbox")
