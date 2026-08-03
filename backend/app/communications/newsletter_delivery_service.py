"""Approved newsletter scheduling and cancellation."""

import os
from typing import Any

from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.communications import newsletter_delivery_repository as repository
from app.communications.newsletter_delivery_schemas import (
    NewsletterScheduleRequest,
)
from app.communications.newsletter_service import create_unsubscribe_token


class NewsletterDeliveryError(ValueError):
    pass


class NewsletterDeliveryNotFoundError(NewsletterDeliveryError):
    pass


class NewsletterDeliveryStateError(NewsletterDeliveryError):
    pass


class NewsletterDeliveryValidationError(NewsletterDeliveryError):
    pass


def get_newsletter(
    *,
    db: Session,
    newsletter_id: int,
) -> dict[str, Any]:
    item = repository.get_newsletter(
        db=db,
        newsletter_id=newsletter_id,
    )

    if item is None:
        raise NewsletterDeliveryNotFoundError(
            "Newsletter not found"
        )

    return item


def active_sender(
    *,
    db: Session,
    sender_identity_id: int,
) -> dict[str, Any]:
    item = repository.get_sender_identity(
        db=db,
        sender_identity_id=sender_identity_id,
    )

    if item is None or not item.get("is_active"):
        raise NewsletterDeliveryValidationError(
            "Sender identity is unavailable"
        )

    return item


def schedule_newsletter(
    *,
    db: Session,
    newsletter_id: int,
    payload: NewsletterScheduleRequest,
    user: CurrentUser,
) -> dict[str, Any]:
    item = get_newsletter(
        db=db,
        newsletter_id=newsletter_id,
    )

    if item["status"] != "approved":
        raise NewsletterDeliveryStateError(
            "Only approved newsletters can be scheduled"
        )

    active_sender(
        db=db,
        sender_identity_id=item["sender_identity_id"],
    )

    recipients = repository.list_eligible_recipients(
        db=db,
        newsletter_id=newsletter_id,
    )

    if not recipients:
        raise NewsletterDeliveryValidationError(
            "Newsletter has no eligible recipients"
        )

    base_url = os.getenv(
        "PUBLIC_API_BASE_URL",
        "http://localhost:8000",
    ).rstrip("/")
    secret = os.getenv(
        "NEWSLETTER_UNSUBSCRIBE_SECRET",
        os.getenv("SECRET_KEY", "development-secret"),
    )

    queued = 0
    existing = 0
    suppressed = 0

    try:
        for recipient in recipients:
            email = recipient["recipient_email"].strip().lower()

            if repository.is_suppressed(
                db=db,
                email_address=email,
            ):
                repository.mark_recipient_suppressed(
                    db=db,
                    recipient_id=recipient["id"],
                    reason="Suppressed during newsletter scheduling",
                )
                suppressed += 1
                continue

            token = create_unsubscribe_token(
                email,
                secret=secret,
            )
            unsubscribe_url = (
                f"{base_url}/api/communications/newsletters/"
                f"unsubscribe/{token}"
            )
            contact_name = (
                recipient.get("recipient_name")
                or "Property Manager"
            )
            company_name = (
                recipient.get("company_name_snapshot")
                or "your company"
            )
            values = {
                "{contact_name}": contact_name,
                "{company_name}": company_name,
                "{unsubscribe_link}": unsubscribe_url,
            }
            body_text = item["body_text"]
            body_html = item["body_html"]

            for placeholder, value in values.items():
                body_text = body_text.replace(placeholder, value)
                body_html = body_html.replace(placeholder, value)

            key = (
                f"newsletter:{newsletter_id}:"
                f"recipient:{recipient['id']}"
            )

            created = repository.queue_newsletter_message(
                db=db,
                newsletter_id=newsletter_id,
                newsletter_recipient_id=recipient["id"],
                lead_id=recipient.get("lead_id"),
                sender_identity_id=item["sender_identity_id"],
                sent_by=item.get("created_by"),
                recipient_email=email,
                recipient_name=recipient.get("recipient_name"),
                subject=item["subject"],
                body_text=body_text,
                body_html=body_html,
                idempotency_key=key,
                scheduled_for=payload.scheduled_for,
            )

            if created:
                queued += 1
                repository.mark_recipient_queued(
                    db=db,
                    recipient_id=recipient["id"],
                    queued_at=payload.scheduled_for,
                )
            else:
                existing += 1

        if queued + existing == 0:
            raise NewsletterDeliveryValidationError(
                "No newsletter messages could be queued"
            )

        repository.mark_newsletter_scheduled(
            db=db,
            newsletter_id=newsletter_id,
            scheduled_for=payload.scheduled_for,
            updated_by=user.id,
        )
        db.commit()

        return {
            "newsletter_id": newsletter_id,
            "status": "scheduled",
            "scheduled_for": payload.scheduled_for,
            "queued_messages": queued,
            "existing_messages": existing,
            "suppressed_recipients": suppressed,
        }

    except Exception:
        db.rollback()
        raise


def cancel_newsletter(
    *,
    db: Session,
    newsletter_id: int,
    user: CurrentUser,
) -> dict[str, Any]:
    item = get_newsletter(
        db=db,
        newsletter_id=newsletter_id,
    )

    if item["status"] not in {"approved", "scheduled", "sending"}:
        raise NewsletterDeliveryStateError(
            "Newsletter cannot be cancelled from its current state"
        )

    repository.cancel_newsletter_and_jobs(
        db=db,
        newsletter_id=newsletter_id,
        updated_by=user.id,
    )
    db.commit()

    return {
        "newsletter_id": newsletter_id,
        "status": "cancelled",
    }
