"""pull_requests.updated_at (ignore stale out-of-order deliveries)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pull_requests", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Existing rows: the newest updated_at among the stored payloads for that PR. Backfilling with
    # created_at instead would let any late, stale delivery overwrite the current state.
    op.execute(
        """
        UPDATE pull_requests p SET updated_at = COALESCE(
            (SELECT max((e.payload -> 'pull_request' ->> 'updated_at')::timestamptz)
             FROM webhook_events e
             WHERE e.event_type = 'pull_request'
               AND (e.payload -> 'pull_request' ->> 'id')::bigint = p.id),
            p.created_at)
        """
    )
    op.alter_column("pull_requests", "updated_at", nullable=False)


def downgrade() -> None:
    op.drop_column("pull_requests", "updated_at")
