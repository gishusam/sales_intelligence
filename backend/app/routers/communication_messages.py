"""Canonical Communications message endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.communications import message_repository
from app.communications import messages as message_service
from app.communications.message_schemas import (
    MessagePreviewRequest,
    MessagePreviewResponse,
    MessageResponse,
    MessageSendRequest,
    MessageSendTestRequest,
)
from app.database import get_db


router = APIRouter(
    prefix="/api/communications/messages",
    tags=["communication-messages"],
)


def translate_error(exc):
    if isinstance(exc, message_service.MessageResourceNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, message_service.RecipientSuppressedError):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, message_service.RecipientNotAllowedError):
        code = status.HTTP_403_FORBIDDEN
    else:
        code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(status_code=code, detail=str(exc))


@router.post("/preview", response_model=MessagePreviewResponse)
def preview_communication_message(
    payload: MessagePreviewRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return message_service.preview_message(
            db=db,
            payload=payload,
            user=user,
        )
    except (message_service.MessageValidationError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.post("/send", response_model=MessageResponse)
def send_communication_message(
    payload: MessageSendRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return message_service.send_message(
            db=db,
            payload=payload,
            user=user,
        )
    except message_service.MessageValidationError as exc:
        raise translate_error(exc) from exc


@router.post("/send-test", response_model=MessageResponse)
def send_test_communication_message(
    payload: MessageSendTestRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return message_service.send_test_message(
            db=db,
            payload=payload,
            user=user,
        )
    except message_service.MessageValidationError as exc:
        raise translate_error(exc) from exc


@router.get("", response_model=list[MessageResponse])
def list_communication_messages(
    lead_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return message_repository.list_messages(
        db=db,
        lead_id=lead_id,
        limit=limit,
    )
