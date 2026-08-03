"""Worker processor for queued newsletter messages."""

from datetime import datetime

from sqlalchemy.orm import Session

from app.communications import newsletter_delivery_repository as repository
from app.communications.delivery import EmailProvider, OutboundMessage
from app.communications.delivery_worker import retry_delay


def process_newsletter_message(
    *,
    db: Session,
    message: dict,
    provider: EmailProvider,
    max_attempts: int,
    now: datetime,
) -> str:
    newsletter_id = message.get("newsletter_id")
    recipient_id = message.get("newsletter_recipient_id")

    item = repository.get_newsletter(
        db=db,
        newsletter_id=newsletter_id,
    )

    if item is None or item["status"] == "cancelled":
        repository.mark_message_dead_letter(
            db=db,
            message_id=message["id"],
            error_message="Newsletter is unavailable or cancelled",
        )
        db.commit()
        return "dead_letter"

    if item["status"] not in {"scheduled", "sending"}:
        repository.mark_message_retry(
            db=db,
            message_id=message["id"],
            error_message="Newsletter is not ready for delivery",
            next_attempt_at=now + retry_delay(
                int(message.get("attempt_count") or 1)
            ),
        )
        db.commit()
        return "retry"

    if repository.is_suppressed(
        db=db,
        email_address=message["recipient_email"],
    ):
        reason = "Recipient suppressed before newsletter delivery"
        repository.mark_message_suppressed(
            db=db,
            message_id=message["id"],
            reason=reason,
        )
        repository.mark_recipient_suppressed(
            db=db,
            recipient_id=recipient_id,
            reason=reason,
        )
        repository.finalize_newsletter_if_complete(
            db=db,
            newsletter_id=newsletter_id,
        )
        db.commit()
        return "suppressed"

    sender = repository.get_sender_identity(
        db=db,
        sender_identity_id=message["sender_identity_id"],
    )
    attempt_count = int(message.get("attempt_count") or 1)

    if sender is None or not sender.get("is_active"):
        error = "Sender identity is unavailable"

        if attempt_count >= max_attempts:
            repository.mark_message_dead_letter(
                db=db,
                message_id=message["id"],
                error_message=error,
            )
            repository.mark_recipient_failed(
                db=db,
                recipient_id=recipient_id,
            )
            repository.finalize_newsletter_if_complete(
                db=db,
                newsletter_id=newsletter_id,
            )
            db.commit()
            return "dead_letter"

        repository.mark_message_retry(
            db=db,
            message_id=message["id"],
            error_message=error,
            next_attempt_at=now + retry_delay(attempt_count),
        )
        db.commit()
        return "retry"

    result = provider.send(
        OutboundMessage(
            from_name=sender["display_name"],
            from_email=sender["email_address"],
            reply_to=sender.get("reply_to_address"),
            to_email=message["recipient_email"],
            subject=message["subject"],
            body_text=message["body_text"],
            body_html=message.get("body_html"),
        )
    )

    if not result.accepted:
        error = result.error_message or "Provider rejected newsletter"

        if attempt_count >= max_attempts:
            repository.mark_message_dead_letter(
                db=db,
                message_id=message["id"],
                error_message=error,
            )
            repository.mark_recipient_failed(
                db=db,
                recipient_id=recipient_id,
            )
            repository.finalize_newsletter_if_complete(
                db=db,
                newsletter_id=newsletter_id,
            )
            db.commit()
            return "dead_letter"

        repository.mark_message_retry(
            db=db,
            message_id=message["id"],
            error_message=error,
            next_attempt_at=now + retry_delay(attempt_count),
        )
        db.commit()
        return "retry"

    repository.mark_message_sent(
        db=db,
        message_id=message["id"],
        provider_message_id=result.provider_message_id,
    )
    repository.mark_recipient_sent(
        db=db,
        recipient_id=recipient_id,
    )
    repository.mark_newsletter_sending(
        db=db,
        newsletter_id=newsletter_id,
    )
    repository.finalize_newsletter_if_complete(
        db=db,
        newsletter_id=newsletter_id,
    )
    db.commit()
    return "sent"
