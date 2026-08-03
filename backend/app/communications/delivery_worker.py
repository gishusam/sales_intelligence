"""Concurrency-safe asynchronous delivery worker."""

from datetime import datetime, timedelta, timezone
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.communications import delivery_worker_repository as repository
from app.communications.delivery import EmailProvider, OutboundMessage
from app.communications.smtp_provider import SMTPEmailProvider
from app.communications.templates import render_template


def retry_delay(attempt_count: int) -> timedelta:
    minutes = min(2 ** max(attempt_count - 1, 0), 360)
    return timedelta(minutes=minutes)


def _personalization(recipient: dict[str, Any]) -> dict[str, str]:
    return {
        "contact_name": recipient.get("recipient_name") or "Property Manager",
        "company_name": recipient.get("company_name_snapshot") or "your company",
        "area": recipient.get("area_snapshot") or "Nairobi",
        "rep_name": recipient.get("rep_name_snapshot") or "Nyumba Zetu Sales",
        "rep_email": recipient.get("rep_email_snapshot") or "",
    }


def _retry_or_dead_letter(
    *,
    db: Session,
    message: dict[str, Any],
    campaign_id: int,
    error: str,
    max_attempts: int,
    now: datetime,
) -> str:
    attempt_count = int(message.get("attempt_count") or 1)

    if attempt_count >= max_attempts:
        repository.mark_message_dead_letter(
            db=db,
            message_id=message["id"],
            error_message=error,
        )
        if message.get("campaign_recipient_id"):
            repository.mark_recipient_failed(
                db=db,
                recipient_id=message["campaign_recipient_id"],
            )
        repository.finalize_campaign_if_complete(
            db=db,
            campaign_id=campaign_id,
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


def process_claimed_message(
    *,
    db: Session,
    message: dict[str, Any],
    provider: EmailProvider,
    max_attempts: int,
    now: datetime,
) -> str:
    if message.get("message_type") == "newsletter":
        from app.communications.newsletter_delivery_worker import (
            process_newsletter_message,
        )

        return process_newsletter_message(
            db=db,
            message=message,
            provider=provider,
            max_attempts=max_attempts,
            now=now,
        )

    campaign_id = message.get("campaign_id")

    if not campaign_id:
        repository.mark_message_dead_letter(
            db=db,
            message_id=message["id"],
            error_message="Worker only processes campaign messages",
        )
        db.commit()
        return "dead_letter"

    campaign = repository.get_campaign(db=db, campaign_id=campaign_id)

    if campaign is None or campaign["status"] == "cancelled":
        repository.mark_message_cancelled(db=db, message_id=message["id"])
        db.commit()
        return "cancelled"

    if campaign["status"] == "paused":
        repository.release_message(
            db=db,
            message_id=message["id"],
            next_attempt_at=now + timedelta(minutes=5),
        )
        db.commit()
        return "deferred"

    if campaign["status"] not in {"scheduled", "running"}:
        repository.mark_message_cancelled(db=db, message_id=message["id"])
        db.commit()
        return "cancelled"

    if repository.is_suppressed(
        db=db,
        email_address=message["recipient_email"],
    ):
        reason = "Recipient suppressed before delivery"
        repository.mark_message_suppressed(
            db=db,
            message_id=message["id"],
            reason=reason,
        )
        if message.get("campaign_recipient_id"):
            repository.mark_recipient_suppressed(
                db=db,
                recipient_id=message["campaign_recipient_id"],
                reason=reason,
            )
        repository.finalize_campaign_if_complete(db=db, campaign_id=campaign_id)
        db.commit()
        return "suppressed"

    sender = repository.get_sender_identity(
        db=db,
        sender_identity_id=message["sender_identity_id"],
    )

    if sender is None or not sender.get("is_active"):
        return _retry_or_dead_letter(
            db=db,
            message=message,
            campaign_id=campaign_id,
            error="Sender identity is unavailable",
            max_attempts=max_attempts,
            now=now,
        )

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
        return _retry_or_dead_letter(
            db=db,
            message=message,
            campaign_id=campaign_id,
            error=result.error_message or "Email provider rejected the message",
            max_attempts=max_attempts,
            now=now,
        )

    repository.mark_message_sent(
        db=db,
        message_id=message["id"],
        provider_message_id=result.provider_message_id,
    )
    repository.mark_campaign_running(db=db, campaign_id=campaign_id)

    if message.get("lead_id"):
        repository.update_lead_after_successful_send(
            db=db,
            lead_id=message["lead_id"],
            changed_by="Communications Worker",
            recipient_email=message["recipient_email"],
        )

    recipient_id = message.get("campaign_recipient_id")
    step_id = message.get("campaign_step_id")

    if recipient_id and step_id:
        recipient = repository.get_recipient(db=db, recipient_id=recipient_id)
        next_step = repository.get_next_frozen_step(
            db=db,
            campaign_id=campaign_id,
            current_step_id=step_id,
        )

        if recipient and next_step:
            subject, body_text = render_template(
                subject=next_step["snapshot_subject"],
                body=next_step["snapshot_body_text"],
                values=_personalization(recipient),
            )
            due_at = now + timedelta(days=next_step["delay_days"])
            key = (
                f"campaign:{campaign_id}:"
                f"recipient:{recipient_id}:"
                f"step:{next_step['id']}"
            )
            created = repository.queue_next_step_message(
                db=db,
                campaign_id=campaign_id,
                campaign_recipient_id=recipient_id,
                campaign_step_id=next_step["id"],
                lead_id=recipient.get("lead_id"),
                template_id=next_step["template_id"],
                sender_identity_id=campaign["sender_identity_id"],
                sent_by=campaign.get("created_by"),
                recipient_email=recipient["recipient_email"],
                recipient_name=recipient.get("recipient_name"),
                subject=subject,
                body_text=body_text,
                body_html=next_step.get("snapshot_body_html"),
                idempotency_key=key,
                scheduled_for=due_at,
            )
            if created:
                repository.mark_recipient_next_step(
                    db=db,
                    recipient_id=recipient_id,
                    step_order=next_step["step_order"],
                    next_run_at=due_at,
                )
        elif recipient:
            repository.mark_recipient_complete(db=db, recipient_id=recipient_id)

    repository.finalize_campaign_if_complete(db=db, campaign_id=campaign_id)
    db.commit()
    return "sent"


def run_delivery_worker(
    *,
    db: Session,
    provider: EmailProvider | None = None,
    batch_size: int = 25,
    worker_id: str | None = None,
    max_attempts: int = 5,
    lock_timeout_minutes: int = 15,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    worker_id = worker_id or f"worker-{uuid.uuid4()}"
    provider = provider or SMTPEmailProvider()

    recovered = repository.requeue_stale_processing(
        db=db,
        stale_before=now - timedelta(minutes=lock_timeout_minutes),
    )
    messages = repository.claim_due_messages(
        db=db,
        limit=batch_size,
        worker_id=worker_id,
        now=now,
    )

    summary = {
        "worker_id": worker_id,
        "recovered": recovered,
        "claimed": len(messages),
        "sent": 0,
        "retried": 0,
        "dead_lettered": 0,
        "suppressed": 0,
        "cancelled": 0,
        "deferred": 0,
        "errors": 0,
    }
    key_map = {
        "sent": "sent",
        "retry": "retried",
        "dead_letter": "dead_lettered",
        "suppressed": "suppressed",
        "cancelled": "cancelled",
        "deferred": "deferred",
    }

    for claimed in messages:
        try:
            outcome = process_claimed_message(
                db=db,
                message=claimed,
                provider=provider,
                max_attempts=max_attempts,
                now=now,
            )
            summary[key_map[outcome]] += 1
        except Exception:
            db.rollback()
            summary["errors"] += 1

    return summary
