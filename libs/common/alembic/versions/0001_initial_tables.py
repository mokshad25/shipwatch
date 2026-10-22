"""initial tables: repos, pull_requests, webhook_events

Revision ID: 0001
Revises:
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repos",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repos")),
    )
    op.create_index(op.f("ix_repos_full_name"), "repos", ["full_name"])

    op.create_table(
        "pull_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("base_branch", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["repos.id"], name=op.f("fk_pull_requests_repo_id_repos")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pull_requests")),
        sa.UniqueConstraint("repo_id", "number", name=op.f("uq_pull_requests_repo_id_number")),
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("delivery_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
    )


def downgrade() -> None:
    op.drop_table("webhook_events")
    op.drop_table("pull_requests")
    op.drop_index(op.f("ix_repos_full_name"), table_name="repos")
    op.drop_table("repos")
