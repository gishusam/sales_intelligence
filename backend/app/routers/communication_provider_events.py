"""Signed provider webhook and authenticated event-history routes."""

import json

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.communications import provider_event_repository
from app.communications import provider_event_service
from app.communications.provider_event_schemas import ProviderEventPayload
from app.database import get_db


router = APIRouter(
    prefix="/api/communications",
    tags=["communication-provider-events"],
)


@router.post("/webhooks/email/{provider}")
async def receive_provider_event(
    provider: str,
    request: Request,
    x_communications_signature: str | None = Header(
        default=None,
        alias="X-Communications-Signature",
    ),
    db: Session = Depends(get_db),
):
    raw_body = await request.body()

    try:
        provider_event_service.authenticate_webhook(
            raw_body=raw_body,
            signature=x_communications_signature,
        )
    except provider_event_service.ProviderWebhookAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    try:
        raw_payload = json.loads(raw_body.decode("utf-8"))
        event = ProviderEventPayload.model_validate(raw_payload)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid normalized provider event payload",
        ) from exc

    return provider_event_service.process_event(
        db=db,
        provider=provider.strip().lower(),
        event=event,
        raw_payload=raw_payload,
    )


@router.get("/events")
def list_provider_events(
    event_type: str | None = Query(default=None),
    message_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return provider_event_repository.list_events(
        db=db,
        event_type=event_type,
        message_id=message_id,
        limit=limit,
    )
