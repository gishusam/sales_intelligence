"""Persistence primitives for concurrency-safe campaign delivery."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


_MESSAGE_FIELDS = (
    "id",
    "newsletter_id",
    "newsletter_recipient_id",
    "campaign_id",
    "campaign_recipient_id",
    "campaign_step_id",
    "lead_id",
    "template_id",
    "sender_identity_id",
    "sent_by",
    "recipient_email",
    "recipient_name",
    "subject",
    "body_text",
    "body_html",
    "message_type",
    "status",
    "idempotency_key",
    "attempt_count",
    "scheduled_for",
)


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)
    return {
        field: getattr(row, field, None)
        for field in _MESSAGE_FIELDS
    }


def requeue_stale_processing(
    *,
    db: Session,
    stale_before,
) -> int:
    row = db.execute(
        text(
            """
            WITH recovered AS (
                UPDATE email_messages
                SET
                    status = 'queued',
                    next_attempt_at = NOW(),
                    locked_at = NULL,
                    locked_by = NULL,
                    error_message = COALESCE(
                        error_message,
                        'Recovered stale worker claim'
                    ),
                    updated_at = NOW()
                WHERE
                    status = 'processing'
                    AND locked_at < :stale_before
                RETURNING id
            )
            SELECT COUNT(*) AS recovered_count
            FROM recovered
            """
        ),
        {"stale_before": stale_before},
    ).fetchone()
    db.commit()
    return int(getattr(row, "recovered_count", 0) or 0)


def claim_due_messages(
    *,
    db: Session,
    limit: int,
    worker_id: str,
    now,
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            WITH due AS (
                SELECT m.id
                FROM email_messages m
                JOIN campaigns c
                    ON c.id = m.campaign_id
                WHERE
                    m.message_type = 'campaign'
                    AND m.status = 'queued'
                    AND c.status IN ('scheduled', 'running')
                    AND COALESCE(
                        m.next_attempt_at,
                        m.scheduled_for,
                        m.created_at
                    ) <= :now
                ORDER BY
                    COALESCE(
                        m.next_attempt_at,
                        m.scheduled_for,
                        m.created_at
                    ),
                    m.id
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            UPDATE email_messages m
            SET
                status = 'processing',
                attempt_count = COALESCE(m.attempt_count, 0) + 1,
                last_attempt_at = :now,
                locked_at = :now,
                locked_by = :worker_id,
                updated_at = :now
            FROM due
            WHERE m.id = due.id
            RETURNING
                m.id,
                m.newsletter_id,
                m.newsletter_recipient_id,
                m.campaign_id,
                m.campaign_recipient_id,
                m.campaign_step_id,
                m.lead_id,
                m.template_id,
                m.sender_identity_id,
                m.sent_by,
                m.recipient_email,
                m.recipient_name,
                m.subject,
                m.body_text,
                m.body_html,
                m.message_type,
                m.status,
                m.idempotency_key,
                m.attempt_count,
                m.scheduled_for
            """
        ),
        {
            "limit": limit,
            "worker_id": worker_id,
            "now": now,
        },
    ).fetchall()
    db.commit()
    return [
        _row_to_dict(row)
        for row in rows
        if row is not None
    ]


def get_campaign(*, db: Session, campaign_id: int) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT id, status, sender_identity_id, created_by
            FROM campaigns
            WHERE id = :campaign_id
            """
        ),
        {"campaign_id": campaign_id},
    ).fetchone()
    if row is None:
        return None
    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)
    return {
        "id": getattr(row, "id", None),
        "status": getattr(row, "status", None),
        "sender_identity_id": getattr(row, "sender_identity_id", None),
        "created_by": getattr(row, "created_by", None),
    }


def get_sender_identity(*, db: Session, sender_identity_id: int) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT
                id,
                display_name,
                email_address,
                reply_to_address,
                provider,
                is_active
            FROM sender_identities
            WHERE id = :sender_identity_id
            """
        ),
        {"sender_identity_id": sender_identity_id},
    ).fetchone()
    if row is None:
        return None
    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)
    return dict(vars(row))


def get_recipient(*, db: Session, recipient_id: int) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT
                id,
                campaign_id,
                lead_id,
                recipient_email,
                recipient_name,
                status,
                company_name_snapshot,
                area_snapshot,
                rep_name_snapshot,
                rep_email_snapshot
            FROM campaign_recipients
            WHERE id = :recipient_id
            """
        ),
        {"recipient_id": recipient_id},
    ).fetchone()
    if row is None:
        return None
    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)
    return dict(vars(row))


def is_suppressed(*, db: Session, email_address: str) -> bool:
    row = db.execute(
        text(
            """
            SELECT TRUE AS suppressed
            FROM suppression_list
            WHERE
                LOWER(email_address) = :email_address
                AND is_active = TRUE
            LIMIT 1
            """
        ),
        {"email_address": email_address.strip().lower()},
    ).fetchone()
    return bool(row)


def release_message(*, db: Session, message_id: int, next_attempt_at) -> None:
    db.execute(
        text(
            """
            UPDATE email_messages
            SET
                status = 'queued',
                attempt_count = GREATEST(attempt_count - 1, 0),
                next_attempt_at = :next_attempt_at,
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE id = :message_id
            """
        ),
        {"message_id": message_id, "next_attempt_at": next_attempt_at},
    )


def mark_message_cancelled(*, db: Session, message_id: int) -> None:
    db.execute(
        text(
            """
            UPDATE email_messages
            SET
                status = 'cancelled',
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE id = :message_id
            """
        ),
        {"message_id": message_id},
    )


def mark_message_suppressed(*, db: Session, message_id: int, reason: str) -> None:
    db.execute(
        text(
            """
            UPDATE email_messages
            SET
                status = 'suppressed',
                error_message = :reason,
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE id = :message_id
            """
        ),
        {"message_id": message_id, "reason": reason},
    )


def mark_recipient_suppressed(*, db: Session, recipient_id: int, reason: str) -> None:
    db.execute(
        text(
            """
            UPDATE campaign_recipients
            SET
                status = 'suppressed',
                suppression_reason = :reason,
                updated_at = NOW()
            WHERE id = :recipient_id
            """
        ),
        {"recipient_id": recipient_id, "reason": reason},
    )


def mark_message_sent(*, db: Session, message_id: int, provider_message_id: str | None) -> None:
    db.execute(
        text(
            """
            UPDATE email_messages
            SET
                status = 'sent',
                provider_message_id = :provider_message_id,
                error_message = NULL,
                sent_at = NOW(),
                next_attempt_at = NULL,
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE id = :message_id
            """
        ),
        {"message_id": message_id, "provider_message_id": provider_message_id},
    )


def mark_message_retry(*, db: Session, message_id: int, error_message: str, next_attempt_at) -> None:
    db.execute(
        text(
            """
            UPDATE email_messages
            SET
                status = 'queued',
                error_message = :error_message,
                next_attempt_at = :next_attempt_at,
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE id = :message_id
            """
        ),
        {
            "message_id": message_id,
            "error_message": error_message[:2000],
            "next_attempt_at": next_attempt_at,
        },
    )


def mark_message_dead_letter(*, db: Session, message_id: int, error_message: str) -> None:
    db.execute(
        text(
            """
            UPDATE email_messages
            SET
                status = 'dead_letter',
                error_message = :error_message,
                dead_lettered_at = NOW(),
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE id = :message_id
            """
        ),
        {"message_id": message_id, "error_message": error_message[:2000]},
    )


def mark_recipient_failed(*, db: Session, recipient_id: int) -> None:
    db.execute(
        text(
            """
            UPDATE campaign_recipients
            SET status = 'failed', updated_at = NOW()
            WHERE id = :recipient_id
            """
        ),
        {"recipient_id": recipient_id},
    )


def update_lead_after_successful_send(
    *,
    db: Session,
    lead_id: int,
    changed_by: str,
    recipient_email: str,
) -> None:
    db.execute(
        text(
            """
            UPDATE leads
            SET
                last_contacted = NOW(),
                contact_attempts = COALESCE(contact_attempts, 0) + 1,
                email_sent_at = NOW(),
                updated_at = NOW()
            WHERE id = :lead_id
            """
        ),
        {"lead_id": lead_id},
    )
    db.execute(
        text(
            """
            INSERT INTO lead_events (
                lead_id,
                event_type,
                to_value,
                changed_by,
                note,
                created_at
            )
            VALUES (
                :lead_id,
                'campaign_email_sent',
                :recipient_email,
                :changed_by,
                'Campaign communication sent',
                NOW()
            )
            """
        ),
        {
            "lead_id": lead_id,
            "recipient_email": recipient_email,
            "changed_by": changed_by,
        },
    )


def get_next_frozen_step(
    *,
    db: Session,
    campaign_id: int,
    current_step_id: int,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT
                next.id,
                next.campaign_id,
                next.step_order,
                next.delay_days,
                next.template_id,
                next.snapshot_subject,
                next.snapshot_body_text,
                next.snapshot_body_html
            FROM campaign_steps current
            JOIN campaign_steps next
                ON next.campaign_id = current.campaign_id
                AND next.step_order = current.step_order + 1
            WHERE
                current.id = :current_step_id
                AND current.campaign_id = :campaign_id
                AND next.snapshot_subject IS NOT NULL
                AND next.snapshot_body_text IS NOT NULL
            """
        ),
        {"campaign_id": campaign_id, "current_step_id": current_step_id},
    ).fetchone()
    if row is None:
        return None
    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)
    return dict(vars(row))


def queue_next_step_message(
    *,
    db: Session,
    campaign_id: int,
    campaign_recipient_id: int,
    campaign_step_id: int,
    lead_id: int | None,
    template_id: int,
    sender_identity_id: int,
    sent_by: int | None,
    recipient_email: str,
    recipient_name: str | None,
    subject: str,
    body_text: str,
    body_html: str | None,
    idempotency_key: str,
    scheduled_for,
) -> bool:
    row = db.execute(
        text(
            """
            INSERT INTO email_messages (
                campaign_id,
                campaign_recipient_id,
                campaign_step_id,
                lead_id,
                template_id,
                sender_identity_id,
                sent_by,
                recipient_email,
                recipient_name,
                subject,
                body_text,
                body_html,
                message_type,
                status,
                idempotency_key,
                scheduled_for,
                next_attempt_at,
                attempt_count,
                created_at,
                updated_at
            )
            VALUES (
                :campaign_id,
                :campaign_recipient_id,
                :campaign_step_id,
                :lead_id,
                :template_id,
                :sender_identity_id,
                :sent_by,
                :recipient_email,
                :recipient_name,
                :subject,
                :body_text,
                :body_html,
                'campaign',
                'queued',
                :idempotency_key,
                :scheduled_for,
                :scheduled_for,
                0,
                NOW(),
                NOW()
            )
            ON CONFLICT (idempotency_key)
                WHERE idempotency_key IS NOT NULL
                DO NOTHING
            RETURNING id
            """
        ),
        {
            "campaign_id": campaign_id,
            "campaign_recipient_id": campaign_recipient_id,
            "campaign_step_id": campaign_step_id,
            "lead_id": lead_id,
            "template_id": template_id,
            "sender_identity_id": sender_identity_id,
            "sent_by": sent_by,
            "recipient_email": recipient_email.strip().lower(),
            "recipient_name": recipient_name,
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "idempotency_key": idempotency_key,
            "scheduled_for": scheduled_for,
        },
    ).fetchone()
    return row is not None


def mark_recipient_next_step(*, db: Session, recipient_id: int, step_order: int, next_run_at) -> None:
    db.execute(
        text(
            """
            UPDATE campaign_recipients
            SET
                status = 'queued',
                next_step_order = :step_order,
                next_run_at = :next_run_at,
                updated_at = NOW()
            WHERE id = :recipient_id
            """
        ),
        {"recipient_id": recipient_id, "step_order": step_order, "next_run_at": next_run_at},
    )


def mark_recipient_complete(*, db: Session, recipient_id: int) -> None:
    db.execute(
        text(
            """
            UPDATE campaign_recipients
            SET
                status = 'sent',
                completed_at = NOW(),
                next_run_at = NULL,
                updated_at = NOW()
            WHERE id = :recipient_id
            """
        ),
        {"recipient_id": recipient_id},
    )


def mark_campaign_running(*, db: Session, campaign_id: int) -> None:
    db.execute(
        text(
            """
            UPDATE campaigns
            SET
                status = 'running',
                started_at = COALESCE(started_at, NOW()),
                updated_at = NOW()
            WHERE id = :campaign_id AND status = 'scheduled'
            """
        ),
        {"campaign_id": campaign_id},
    )


def finalize_campaign_if_complete(*, db: Session, campaign_id: int) -> None:
    db.execute(
        text(
            """
            UPDATE campaigns
            SET
                status = 'completed',
                completed_at = NOW(),
                updated_at = NOW()
            WHERE
                id = :campaign_id
                AND status IN ('scheduled', 'running')
                AND NOT EXISTS (
                    SELECT 1
                    FROM campaign_recipients
                    WHERE
                        campaign_id = :campaign_id
                        AND status IN ('enrolled', 'queued')
                )
            """
        ),
        {"campaign_id": campaign_id},
    )
