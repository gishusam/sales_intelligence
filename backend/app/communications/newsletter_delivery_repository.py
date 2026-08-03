"""Newsletter scheduling and delivery persistence."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def row_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None

    if isinstance(row, dict):
        return dict(row)

    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)

    return dict(vars(row))


def get_newsletter(
    *,
    db: Session,
    newsletter_id: int,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT
                id,
                name,
                subject,
                status,
                sender_identity_id,
                body_text,
                body_html,
                created_by,
                scheduled_for
            FROM newsletter_drafts
            WHERE id = :newsletter_id
            """
        ),
        {"newsletter_id": newsletter_id},
    ).fetchone()

    return row_dict(row)


def get_sender_identity(
    *,
    db: Session,
    sender_identity_id: int,
) -> dict[str, Any] | None:
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

    return row_dict(row)


def list_eligible_recipients(
    *,
    db: Session,
    newsletter_id: int,
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT
                id,
                newsletter_id,
                lead_id,
                recipient_email,
                recipient_name,
                company_name_snapshot,
                status
            FROM newsletter_recipients
            WHERE
                newsletter_id = :newsletter_id
                AND status = 'eligible'
            ORDER BY id
            """
        ),
        {"newsletter_id": newsletter_id},
    ).fetchall()

    return [row_dict(row) for row in rows if row is not None]


def is_suppressed(
    *,
    db: Session,
    email_address: str,
) -> bool:
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


def mark_recipient_suppressed(
    *,
    db: Session,
    recipient_id: int,
    reason: str,
) -> None:
    db.execute(
        text(
            """
            UPDATE newsletter_recipients
            SET
                status = 'suppressed',
                suppression_reason = :reason,
                updated_at = NOW()
            WHERE id = :recipient_id
            """
        ),
        {"recipient_id": recipient_id, "reason": reason},
    )


def queue_newsletter_message(
    *,
    db: Session,
    newsletter_id: int,
    newsletter_recipient_id: int,
    lead_id: int | None,
    sender_identity_id: int,
    sent_by: int | None,
    recipient_email: str,
    recipient_name: str | None,
    subject: str,
    body_text: str,
    body_html: str,
    idempotency_key: str,
    scheduled_for,
) -> bool:
    row = db.execute(
        text(
            """
            INSERT INTO email_messages (
                newsletter_id,
                newsletter_recipient_id,
                lead_id,
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
                :newsletter_id,
                :newsletter_recipient_id,
                :lead_id,
                :sender_identity_id,
                :sent_by,
                :recipient_email,
                :recipient_name,
                :subject,
                :body_text,
                :body_html,
                'newsletter',
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
            "newsletter_id": newsletter_id,
            "newsletter_recipient_id": newsletter_recipient_id,
            "lead_id": lead_id,
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


def mark_recipient_queued(
    *,
    db: Session,
    recipient_id: int,
    queued_at,
) -> None:
    db.execute(
        text(
            """
            UPDATE newsletter_recipients
            SET
                status = 'queued',
                queued_at = :queued_at,
                updated_at = NOW()
            WHERE id = :recipient_id
            """
        ),
        {"recipient_id": recipient_id, "queued_at": queued_at},
    )


def mark_newsletter_scheduled(
    *,
    db: Session,
    newsletter_id: int,
    scheduled_for,
    updated_by: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE newsletter_drafts
            SET
                status = 'scheduled',
                scheduled_for = :scheduled_for,
                updated_by = :updated_by,
                updated_at = NOW()
            WHERE id = :newsletter_id
            """
        ),
        {
            "newsletter_id": newsletter_id,
            "scheduled_for": scheduled_for,
            "updated_by": updated_by,
        },
    )


def mark_newsletter_sending(
    *,
    db: Session,
    newsletter_id: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE newsletter_drafts
            SET
                status = 'sending',
                started_at = COALESCE(started_at, NOW()),
                updated_at = NOW()
            WHERE
                id = :newsletter_id
                AND status = 'scheduled'
            """
        ),
        {"newsletter_id": newsletter_id},
    )


def mark_message_sent(
    *,
    db: Session,
    message_id: int,
    provider_message_id: str | None,
) -> None:
    db.execute(
        text(
            """
            UPDATE email_messages
            SET
                status = 'sent',
                provider_message_id = :provider_message_id,
                sent_at = NOW(),
                error_message = NULL,
                locked_at = NULL,
                locked_by = NULL,
                updated_at = NOW()
            WHERE id = :message_id
            """
        ),
        {
            "message_id": message_id,
            "provider_message_id": provider_message_id,
        },
    )


def mark_message_suppressed(
    *,
    db: Session,
    message_id: int,
    reason: str,
) -> None:
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


def mark_message_retry(
    *,
    db: Session,
    message_id: int,
    error_message: str,
    next_attempt_at,
) -> None:
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


def mark_message_dead_letter(
    *,
    db: Session,
    message_id: int,
    error_message: str,
) -> None:
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


def mark_recipient_sent(
    *,
    db: Session,
    recipient_id: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE newsletter_recipients
            SET
                status = 'sent',
                sent_at = NOW(),
                updated_at = NOW()
            WHERE id = :recipient_id
            """
        ),
        {"recipient_id": recipient_id},
    )


def mark_recipient_failed(
    *,
    db: Session,
    recipient_id: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE newsletter_recipients
            SET
                status = 'failed',
                failed_at = NOW(),
                updated_at = NOW()
            WHERE id = :recipient_id
            """
        ),
        {"recipient_id": recipient_id},
    )


def finalize_newsletter_if_complete(
    *,
    db: Session,
    newsletter_id: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE newsletter_drafts
            SET
                status = 'sent',
                sent_at = NOW(),
                updated_at = NOW()
            WHERE
                id = :newsletter_id
                AND status IN ('scheduled', 'sending')
                AND NOT EXISTS (
                    SELECT 1
                    FROM newsletter_recipients
                    WHERE
                        newsletter_id = :newsletter_id
                        AND status IN ('eligible', 'queued')
                )
            """
        ),
        {"newsletter_id": newsletter_id},
    )


def cancel_newsletter_and_jobs(
    *,
    db: Session,
    newsletter_id: int,
    updated_by: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE newsletter_drafts
            SET
                status = 'cancelled',
                cancelled_at = NOW(),
                updated_by = :updated_by,
                updated_at = NOW()
            WHERE id = :newsletter_id
            """
        ),
        {"newsletter_id": newsletter_id, "updated_by": updated_by},
    )

    db.execute(
        text(
            """
            UPDATE newsletter_recipients
            SET
                status = 'cancelled',
                updated_at = NOW()
            WHERE
                newsletter_id = :newsletter_id
                AND status IN ('eligible', 'queued')
            """
        ),
        {"newsletter_id": newsletter_id},
    )

    db.execute(
        text(
            """
            UPDATE email_messages
            SET
                status = 'cancelled',
                updated_at = NOW()
            WHERE
                newsletter_id = :newsletter_id
                AND status IN ('queued', 'processing')
            """
        ),
        {"newsletter_id": newsletter_id},
    )
