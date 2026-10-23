import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from shipwatch_common.kafka import EventEnvelope
from shipwatch_common.logging import get_logger
from shipwatch_common.settings import Settings, get_settings

from ingester.persistence import (
    DuplicateDeliveryError,
    enqueue_event,
    record_delivery,
    upsert_pull_request,
)
from ingester.signature import verify_signature

log = get_logger(__name__)
router = APIRouter()


async def read_body(request: Request, limit: int) -> bytes:
    """Read the raw body, refusing (413) anything over `limit` bytes without buffering it all."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="payload too large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:  # no or lying Content-Length (e.g. chunked transfer)
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="payload too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/webhooks/github", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    x_github_event: Annotated[str | None, Header()] = None,
    x_github_delivery: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    # The signature covers the exact bytes GitHub sent, so read them before any parsing.
    body = await read_body(request, settings.max_body_bytes)
    secret = settings.github_webhook_secret.get_secret_value().encode()
    if not verify_signature(secret, body, x_hub_signature_256):
        log.warning("webhook rejected", reason="invalid signature")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid signature")
    if not x_github_delivery:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="missing X-GitHub-Delivery")
    if not x_github_event:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="missing X-GitHub-Event")

    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        payload = None
    if not isinstance(payload, dict):
        log.warning("webhook rejected", reason="malformed JSON")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="malformed JSON")

    is_pull_request = x_github_event == "pull_request"
    try:
        async with request.app.state.sessionmaker.begin() as session:
            await record_delivery(
                session, delivery_id=x_github_delivery, event_type=x_github_event, payload=payload
            )
            if is_pull_request:
                await upsert_pull_request(session, payload)
                envelope = EventEnvelope(
                    delivery_id=x_github_delivery,
                    type=f"{x_github_event}.{payload.get('action')}",
                    payload=payload,
                )
                # Same transaction as the delivery: published by the relay iff this commits.
                await enqueue_event(
                    session,
                    topic=settings.pr_events_topic,
                    key=payload["repository"]["full_name"],
                    envelope=envelope,
                )
    except DuplicateDeliveryError:
        log.info("webhook duplicate")
        response.status_code = status.HTTP_200_OK
        return {"status": "duplicate"}

    if not is_pull_request:
        log.info("webhook ignored", action=payload.get("action"))
        return {"status": "ignored"}
    # Nudge the relay so it publishes now rather than at its next poll; never wait on Kafka here.
    relay = getattr(request.app.state, "relay", None)
    if relay is not None:
        relay.wake()
    log.info("webhook stored", action=payload.get("action"), event_id=str(envelope.event_id))
    return {"status": "accepted"}
