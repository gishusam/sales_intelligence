"""Webhook authentication, idempotency, and provider-event policy."""

import hashlib
import hmac
import os
from typing import Any

from sqlalchemy.orm import Session

from app.communications import provider_event_repository as repository
from app.communications.provider_event_schemas import ProviderEventPayload


_SUPPRESSING_EVENTS = {
    "hard_bounce",
    "complaint",
    "unsubscribe",
}


class ProviderEventError(ValueError):
    pass


class ProviderWebhookAuthenticationError(ProviderEventError):
    pass


def create_webhook_signature(
    raw_body: bytes,
    *,
    secret: str,
) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return f"sha256={digest}"


def verify_webhook_signature(
    raw_body: bytes,
    signature: str,
    *,
    secret: str,
) -> bool:
    expected = create_webhook_signature(
        raw_body,
        secret=secret,
    )
    normalized = (
        signature
        if signature.startswith("sha256=")
        else f"sha256={signature}"
    )

    return hmac.compare_digest(expected, normalized)


def authenticate_webhook(
    *,
    raw_body: bytes,
    signature: str | None,
) -> None:
    secret = os.getenv("EMAIL_WEBHOOK_SECRET", "").strip()

    if not secret:
        raise ProviderWebhookAuthenticationError(
            "EMAIL_WEBHOOK_SECRET is not configured"
        )

    if not signature or not verify_webhook_signature(
        raw_body,
        signature,
        secret=secret,
    ):
        raise ProviderWebhookAuthenticationError(
            "Invalid provider webhook signature"
        )


def process_event(
    *,
    db: Session,
    provider: str,
    event: ProviderEventPayload,
    raw_payload: dict[str, Any],
) -> dict[str, Any]:
    existing = repository.find_event(
        db=db,
        provider=provider,
        provider_event_id=event.provider_event_id,
    )

    if existing is not None:
        return {
            "event_id": existing["id"],
            "message_id": existing.get("email_message_id"),
            "status": existing.get("status", "processed"),
            "duplicate": True,
        }

    message = repository.find_message_by_provider_id(
        db=db,
        provider_message_id=event.provider_message_id,
    )
    message_id = message["id"] if message else None
    event_status = "processed" if message else "orphaned"

    try:
        event_id = repository.create_event(
            db=db,
            provider=provider,
            provider_event_id=event.provider_event_id,
            email_message_id=message_id,
            provider_message_id=event.provider_message_id,
            event_type=event.event_type,
            recipient_email=event.recipient_email,
            bounce_type=event.bounce_type,
            reason=event.reason,
            url=event.url,
            occurred_at=event.occurred_at,
            payload=raw_payload,
            signature_verified=True,
            status=event_status,
        )

        if message is not None:
            repository.apply_message_event(
                db=db,
                message_id=message_id,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                bounce_type=event.bounce_type,
                reason=event.reason,
            )
            repository.update_recipient_from_event(
                db=db,
                message=message,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                reason=event.reason,
            )

        recipient_email = (
            event.recipient_email
            or (
                message.get("recipient_email")
                if message
                else None
            )
        )

        if (
            event.event_type in _SUPPRESSING_EVENTS
            and recipient_email
        ):
            repository.suppress_email(
                db=db,
                email_address=recipient_email,
                reason=(
                    event.reason
                    or f"Provider event: {event.event_type}"
                ),
                source=f"provider_{event.event_type}",
            )

        db.commit()

        return {
            "event_id": event_id,
            "message_id": message_id,
            "status": event_status,
            "duplicate": False,
        }

    except Exception:
        db.rollback()
        raise
