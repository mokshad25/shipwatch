from datetime import datetime
from typing import Any

from shipwatch_common.kafka import EventEnvelope
from shipwatch_common.models import Outbox, PullRequest, Repo, WebhookEvent
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

DELIVERY_ID_CONSTRAINT = "uq_webhook_events_delivery_id"


class DuplicateDeliveryError(Exception):
    """This X-GitHub-Delivery was already stored."""


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


async def record_delivery(
    session: AsyncSession, *, delivery_id: str, event_type: str, payload: dict[str, Any]
) -> None:
    """Insert the webhook_events row for this delivery. Caller owns the transaction.

    Call this first in the transaction so a duplicate delivery fails before touching anything
    else. We don't check-then-insert: the unique constraint decides, which also covers two
    identical deliveries racing (the second INSERT waits for the first transaction, then
    violates). Raises DuplicateDeliveryError; the caller's transaction must then be rolled back.
    """
    session.add(
        WebhookEvent(
            delivery_id=delivery_id,
            event_type=event_type,
            action=payload.get("action"),
            payload=payload,
        )
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        if DELIVERY_ID_CONSTRAINT not in str(exc.orig):
            raise
        raise DuplicateDeliveryError(delivery_id) from exc


async def upsert_pull_request(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Upsert the repo and pull request described by a pull_request event payload.

    GitHub doesn't guarantee delivery order, so the PR row is only overwritten by a payload whose
    `updated_at` is not older than the stored one; a late, stale delivery leaves it alone (the
    delivery itself is still recorded in webhook_events).
    """
    repo = payload["repository"]
    repo_stmt = insert(Repo).values(id=repo["id"], full_name=repo["full_name"])
    await session.execute(
        repo_stmt.on_conflict_do_update(
            index_elements=[Repo.id], set_={"full_name": repo_stmt.excluded.full_name}
        )
    )

    pr = payload["pull_request"]
    values = {
        "repo_id": repo["id"],
        "number": pr["number"],
        "title": pr["title"],
        "author": pr["user"]["login"],
        "state": pr["state"],
        "head_sha": pr["head"]["sha"],
        "base_branch": pr["base"]["ref"],
        "created_at": _ts(pr["created_at"]),
        "merged_at": _ts(pr.get("merged_at")),
        "updated_at": _ts(pr["updated_at"]),
    }
    pr_stmt = insert(PullRequest).values(id=pr["id"], **values)
    await session.execute(
        pr_stmt.on_conflict_do_update(
            index_elements=[PullRequest.id],
            set_={k: pr_stmt.excluded[k] for k in values},
            where=PullRequest.updated_at <= pr_stmt.excluded.updated_at,
        )
    )


async def enqueue_event(
    session: AsyncSession, *, topic: str, key: str, envelope: EventEnvelope
) -> None:
    """Add an outbox row for `envelope`. Caller owns the transaction: the row commits (and is later
    published by the relay) only if the rest of the transaction does."""
    session.add(
        Outbox(
            event_id=envelope.event_id,
            topic=topic,
            key=key,
            envelope=envelope.model_dump(mode="json"),
        )
    )
