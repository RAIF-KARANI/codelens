"""POST /webhooks/github: where GitHub tells us something happened.

Authenticated by the delivery's HMAC signature, not a JWT. The handler only
verifies, records the state an event describes, and queues the jobs it calls
for (core.github.webhooks.apply_event); the worker does the real work, so
GitHub always gets its answer well within its 10-second timeout.
"""

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_queue, get_session, get_settings
from api.errors import ApiError
from api.queue import JobQueue
from core.config import Settings
from core.db.models import WebhookDelivery
from core.github.webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    apply_event,
    verify_signature,
)

router = APIRouter(tags=["webhooks"])

deliveries_t = WebhookDelivery.__table__


class WebhookAck(BaseModel):
    delivery_id: str
    event: str
    # processed: acted on. ignored: recorded, nothing to do (yet).
    # duplicate: already handled; GitHub sent it again.
    status: Literal["processed", "ignored", "duplicate"]


@router.post("/webhooks/github", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
    queue: Annotated[JobQueue, Depends(get_queue)],
) -> WebhookAck:
    if settings.demo_mode:
        raise ApiError(403, "demo_mode", "this instance does not accept webhooks")
    secret = settings.github_webhook_secret.get_secret_value()
    if not secret:
        # Fail closed, as the API does without its JWT secret.
        raise ApiError(503, "webhook_not_configured", "GITHUB_WEBHOOK_SECRET is not set")

    # The signature covers the exact bytes GitHub sent, so verify those
    # before parsing anything.
    body = await request.body()
    if not verify_signature(secret, body, request.headers.get(SIGNATURE_HEADER)):
        raise ApiError(401, "invalid_signature", "the delivery's signature does not match")
    delivery_id = request.headers.get(DELIVERY_HEADER)
    event = request.headers.get(EVENT_HEADER)
    if not delivery_id or not event:
        raise ApiError(400, "missing_header", f"{DELIVERY_HEADER} and {EVENT_HEADER} are required")
    try:
        payload: Any = json.loads(body)
    except ValueError as exc:
        raise ApiError(400, "invalid_payload", "the body is not JSON") from exc
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_payload", "the body is not a JSON object")

    action = payload.get("action") if isinstance(payload.get("action"), str) else None
    installation = payload.get("installation")
    installation_id = installation.get("id") if isinstance(installation, dict) else None

    # Claim the delivery. A redelivery of one already handled finds a row with
    # processed_at set and does nothing; one that failed halfway (no
    # processed_at) is handled again (D95).
    conn = await session.connection()
    claimed = await conn.scalar(
        insert(deliveries_t)
        .values(
            delivery_id=delivery_id, event=event, action=action, installation_id=installation_id
        )
        .on_conflict_do_update(
            index_elements=[deliveries_t.c.delivery_id],
            set_={"received_at": func.now()},
            where=deliveries_t.c.processed_at.is_(None),
        )
        .returning(deliveries_t.c.delivery_id)
    )
    if claimed is None:
        await session.rollback()
        return WebhookAck(delivery_id=delivery_id, event=event, status="duplicate")

    # Three steps, in this order, so no failure loses work (D98):
    # 1. the claim and the state the event describes, committed together;
    # 2. the jobs it calls for (a broker failure here is a 503, and the
    #    delivery stays unprocessed, so a redelivery queues them again; every
    #    job is idempotent);
    # 3. only then, mark the delivery processed.
    outcome = await apply_event(conn, event, action, payload)
    await session.commit()
    for repo_id in outcome.index:
        await queue.index_repo(repo_id)
    await session.execute(
        update(deliveries_t)
        .where(deliveries_t.c.delivery_id == delivery_id)
        .values(processed_at=func.now())
    )
    await session.commit()
    return WebhookAck(delivery_id=delivery_id, event=event, status=outcome.status)
