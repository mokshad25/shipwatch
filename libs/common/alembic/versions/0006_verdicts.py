"""verdicts table (PR risk agent output, unique per pr_id + head_sha)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "verdicts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("pr_id", sa.BigInteger(), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("assessment", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "cost_usd", sa.NUMERIC(precision=10, scale=6), server_default="0", nullable=False
        ),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["pr_id"], ["pull_requests.id"], name=op.f("fk_verdicts_pr_id_pull_requests")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verdicts")),
        sa.UniqueConstraint("pr_id", "head_sha", name=op.f("uq_verdicts_pr_id_head_sha")),
    )


def downgrade() -> None:
    op.drop_table("verdicts")
