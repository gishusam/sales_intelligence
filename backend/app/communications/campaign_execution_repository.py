"""Persistence for campaign preflight, lifecycle, and queued jobs."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None

    if isinstance(row, dict):
        return dict(row)

    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)

    return dict(vars(row))


def get_campaign(
    *,
    db: Session,
    campaign_id: int,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT
                id,
                name,
                description,
                campaign_type,
                sender_identity_id,
                status,
                created_by,
                updated_by,
                prepared_at,
                scheduled_for,
                started_at,
                paused_at,
                cancelled_at,
                paused_from_status,
                created_at,
                updated_at
            FROM campaigns
            WHERE id = :campaign_id
            """
        ),
        {"campaign_id": campaign_id},
    ).fetchone()

    return _row_to_dict(row)


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

    return _row_to_dict(row)


def list_steps_for_preflight(
    *,
    db: Session,
    campaign_id: int,
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT
                s.id,
                s.campaign_id,
                s.step_order,
                s.delay_days,
                s.template_id,
                s.subject_override,
                s.body_text_override,
                s.snapshot_subject,
                s.snapshot_body_text,
                s.snapshot_body_html,
                s.snapshot_template_version,
                t.template_type,
                t.is_active AS template_is_active,
                t.version AS template_version,
                t.subject AS template_subject,
                t.body_text AS template_body_text,
                t.body_html AS template_body_html
            FROM campaign_steps s
            JOIN communication_templates t
                ON t.id = s.template_id
            WHERE s.campaign_id = :campaign_id
            ORDER BY s.step_order
            """
        ),
        {"campaign_id": campaign_id},
    ).fetchall()

    return [
        _row_to_dict(row)
        for row in rows
        if row is not None
    ]


def freeze_step_snapshot(
    *,
    db: Session,
    step_id: int,
    subject: str,
    body_text: str,
    body_html: str | None,
    template_version: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE campaign_steps
            SET
                snapshot_subject = :subject,
                snapshot_body_text = :body_text,
                snapshot_body_html = :body_html,
                snapshot_template_version =
                    :template_version,
                updated_at = NOW()
            WHERE id = :step_id
            """
        ),
        {
            "step_id": step_id,
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "template_version": template_version,
        },
    )


def freeze_recipient_personalization(
    *,
    db: Session,
    campaign_id: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE campaign_recipients r
            SET
                company_name_snapshot = COALESCE(
                    (
                        SELECT l.name
                        FROM leads l
                        WHERE l.id = r.lead_id
                    ),
                    'your company'
                ),
                area_snapshot = COALESCE(
                    (
                        SELECT l.area
                        FROM leads l
                        WHERE l.id = r.lead_id
                    ),
                    'Nairobi'
                ),
                rep_name_snapshot = COALESCE(
                    (
                        SELECT u.name
                        FROM campaigns c
                        LEFT JOIN users u
                            ON u.id = c.created_by
                        WHERE c.id = r.campaign_id
                    ),
                    'Nyumba Zetu Sales'
                ),
                rep_email_snapshot = COALESCE(
                    (
                        SELECT u.email
                        FROM campaigns c
                        LEFT JOIN users u
                            ON u.id = c.created_by
                        WHERE c.id = r.campaign_id
                    ),
                    ''
                ),
                updated_at = NOW()
            WHERE
                r.campaign_id = :campaign_id
                AND r.status = 'enrolled'
            """
        ),
        {"campaign_id": campaign_id},
    )


def list_enrolled_recipients(
    *,
    db: Session,
    campaign_id: int,
) -> list[dict[str, Any]]:
    rows = db.execute(
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
            WHERE
                campaign_id = :campaign_id
                AND status = 'enrolled'
            ORDER BY id
            """
        ),
        {"campaign_id": campaign_id},
    ).fetchall()

    return [
        _row_to_dict(row)
        for row in rows
        if row is not None
    ]


def list_eligible_recipients(
    *,
    db: Session,
    campaign_id: int,
) -> list[dict[str, Any]]:
    return list_enrolled_recipients(
        db=db,
        campaign_id=campaign_id,
    )


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
                LOWER(email_address) =
                    :email_address
                AND is_active = TRUE
            LIMIT 1
            """
        ),
        {
            "email_address": (
                email_address.strip().lower()
            )
        },
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
            UPDATE campaign_recipients
            SET
                status = 'suppressed',
                suppression_reason = :reason,
                updated_at = NOW()
            WHERE id = :recipient_id
            """
        ),
        {
            "recipient_id": recipient_id,
            "reason": reason,
        },
    )


def mark_campaign_ready(
    *,
    db: Session,
    campaign_id: int,
    updated_by: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE campaigns
            SET
                status = 'ready',
                prepared_at = NOW(),
                updated_by = :updated_by,
                updated_at = NOW()
            WHERE id = :campaign_id
            """
        ),
        {
            "campaign_id": campaign_id,
            "updated_by": updated_by,
        },
    )


def get_first_frozen_step(
    *,
    db: Session,
    campaign_id: int,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT
                id,
                campaign_id,
                step_order,
                delay_days,
                template_id,
                snapshot_subject,
                snapshot_body_text,
                snapshot_body_html,
                snapshot_template_version
            FROM campaign_steps
            WHERE
                campaign_id = :campaign_id
                AND snapshot_subject IS NOT NULL
                AND snapshot_body_text IS NOT NULL
            ORDER BY step_order
            LIMIT 1
            """
        ),
        {"campaign_id": campaign_id},
    ).fetchone()

    return _row_to_dict(row)


def queue_campaign_message(
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
            "campaign_recipient_id": (
                campaign_recipient_id
            ),
            "campaign_step_id": campaign_step_id,
            "lead_id": lead_id,
            "template_id": template_id,
            "sender_identity_id": sender_identity_id,
            "sent_by": sent_by,
            "recipient_email": (
                recipient_email.strip().lower()
            ),
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
    step_order: int,
    next_run_at,
) -> None:
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
        {
            "recipient_id": recipient_id,
            "step_order": step_order,
            "next_run_at": next_run_at,
        },
    )


def mark_campaign_scheduled(
    *,
    db: Session,
    campaign_id: int,
    scheduled_for,
    updated_by: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE campaigns
            SET
                status = 'scheduled',
                scheduled_for = :scheduled_for,
                updated_by = :updated_by,
                updated_at = NOW()
            WHERE id = :campaign_id
            """
        ),
        {
            "campaign_id": campaign_id,
            "scheduled_for": scheduled_for,
            "updated_by": updated_by,
        },
    )


def mark_campaign_paused(
    *,
    db: Session,
    campaign_id: int,
    previous_status: str,
    updated_by: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE campaigns
            SET
                status = 'paused',
                paused_from_status = :previous_status,
                paused_at = NOW(),
                updated_by = :updated_by,
                updated_at = NOW()
            WHERE id = :campaign_id
            """
        ),
        {
            "campaign_id": campaign_id,
            "previous_status": previous_status,
            "updated_by": updated_by,
        },
    )


def mark_campaign_resumed(
    *,
    db: Session,
    campaign_id: int,
    target_status: str,
    updated_by: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE campaigns
            SET
                status = :target_status,
                paused_from_status = NULL,
                paused_at = NULL,
                updated_by = :updated_by,
                updated_at = NOW()
            WHERE id = :campaign_id
            """
        ),
        {
            "campaign_id": campaign_id,
            "target_status": target_status,
            "updated_by": updated_by,
        },
    )


def cancel_campaign_and_pending_jobs(
    *,
    db: Session,
    campaign_id: int,
    updated_by: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE campaigns
            SET
                status = 'cancelled',
                cancelled_at = NOW(),
                updated_by = :updated_by,
                updated_at = NOW()
            WHERE id = :campaign_id
            """
        ),
        {
            "campaign_id": campaign_id,
            "updated_by": updated_by,
        },
    )

    db.execute(
        text(
            """
            UPDATE campaign_recipients
            SET
                status = 'cancelled',
                updated_at = NOW()
            WHERE
                campaign_id = :campaign_id
                AND status IN ('enrolled', 'queued')
            """
        ),
        {"campaign_id": campaign_id},
    )

    db.execute(
        text(
            """
            UPDATE email_messages
            SET
                status = 'cancelled',
                updated_at = NOW()
            WHERE
                campaign_id = :campaign_id
                AND status = 'queued'
            """
        ),
        {"campaign_id": campaign_id},
    )
