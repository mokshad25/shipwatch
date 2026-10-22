import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from shipwatch_common.settings import Settings, get_settings

from ingester.persistence import DuplicateDeliveryError, store_pull_request_event
from ingester.signature import verify_signature

log = logging.getLogger(__name__)
router = APIRouter()


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
    body = await request.body()
    secret = settings.github_webhook_secret.get_secret_value().encode()
    if not verify_signature(secret, body, x_hub_signature_256):
        log.warning("rejected webhook: invalid signature delivery_id=%s", x_github_delivery)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid signature")
    if not x_github_delivery:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="missing X-GitHub-Delivery")

    if x_github_event != "pull_request":
        log.info("ignored webhook event=%s delivery_id=%s", x_github_event, x_github_delivery)
        return {"status": "accepted"}

    payload = json.loads(body)
    try:
        async with request.app.state.sessionmaker.begin() as session:
            await store_pull_request_event(session, delivery_id=x_github_delivery, payload=payload)
    except DuplicateDeliveryError:
        log.info("duplicate webhook delivery_id=%s", x_github_delivery)
        response.status_code = status.HTTP_200_OK
        return {"status": "duplicate"}

    log.info(
        "stored webhook event=%s action=%s delivery_id=%s",
        x_github_event,
        payload.get("action"),
        x_github_delivery,
    )
    return {"status": "accepted"}
