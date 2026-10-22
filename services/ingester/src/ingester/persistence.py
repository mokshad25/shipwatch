from datetime import datetime
from typing import Any

from shipwatch_common.models import PullRequest, Repo, WebhookEvent
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

DELIVERY_ID_CONSTRAINT = "uq_webhook_events_delivery_id"


class DuplicateDeliveryError(Exception):
    """This X-GitHub-Delivery was already stored."""


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


async def store_pull_request_event(
    session: AsyncSession, *, delivery_id: str, payload: dict[str, Any]
) -> None:
    """Record the delivery and upsert its repo and pull request. Caller owns the transaction.

    The event row goes first so a duplicate delivery fails before touching anything else. We
    don't check-then-insert: the unique constraint decides, which also covers two identical
    deliveries racing (the second INSERT waits for the first transaction, then violates).
    Raises DuplicateDeliveryError; the caller's transaction must then be rolled back.
    """
    session.add(
        WebhookEvent(
            delivery_id=delivery_id,
            event_type="pull_request",
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
    }
    pr_stmt = insert(PullRequest).values(id=pr["id"], **values)
    await session.execute(
        pr_stmt.on_conflict_do_update(
            index_elements=[PullRequest.id],
            set_={k: pr_stmt.excluded[k] for k in values},
        )
    )
