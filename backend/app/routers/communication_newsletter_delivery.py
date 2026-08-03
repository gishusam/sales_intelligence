"""Approved newsletter scheduling and cancellation routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.communications import newsletter_delivery_service
from app.communications.newsletter_delivery_schemas import (
    NewsletterScheduleRequest,
)
from app.database import get_db


router = APIRouter(
    prefix="/api/communications/newsletters",
    tags=["communication-newsletter-delivery"],
)


def require_editor(user: CurrentUser) -> None:
    if user.role not in ("admin", "manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager or admin access required",
        )


def translate_error(exc):
    if isinstance(
        exc,
        newsletter_delivery_service.NewsletterDeliveryNotFoundError,
    ):
        return HTTPException(404, str(exc))

    if isinstance(
        exc,
        newsletter_delivery_service.NewsletterDeliveryStateError,
    ):
        return HTTPException(409, str(exc))

    return HTTPException(422, str(exc))


@router.post("/{newsletter_id}/schedule")
def schedule_newsletter(
    newsletter_id: int,
    payload: NewsletterScheduleRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)

    try:
        return newsletter_delivery_service.schedule_newsletter(
            db=db,
            newsletter_id=newsletter_id,
            payload=payload,
            user=user,
        )
    except newsletter_delivery_service.NewsletterDeliveryError as exc:
        raise translate_error(exc) from exc


@router.post("/{newsletter_id}/cancel")
def cancel_newsletter(
    newsletter_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)

    try:
        return newsletter_delivery_service.cancel_newsletter(
            db=db,
            newsletter_id=newsletter_id,
            user=user,
        )
    except newsletter_delivery_service.NewsletterDeliveryError as exc:
        raise translate_error(exc) from exc
