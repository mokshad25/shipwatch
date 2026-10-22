"""unique webhook_events.delivery_id (idempotency key)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_webhook_events_delivery_id"), "webhook_events", ["delivery_id"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_webhook_events_delivery_id"), "webhook_events", type_="unique")
