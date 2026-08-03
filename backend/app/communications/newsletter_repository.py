"""Newsletter persistence."""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import CurrentUser


_FIELDS = (
    "id",
    "name",
    "subject",
    "preview_text",
    "status",
    "sender_identity_id",
    "blocks",
    "body_text",
    "body_html",
    "created_by",
    "updated_by",
    "reviewed_by",
    "approved_by",
    "reviewed_at",
    "approved_at",
    "created_at",
    "updated_at",
)


def row_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None

    if isinstance(row, dict):
        item = dict(row)
    elif getattr(row, "_mapping", None) is not None:
        item = dict(row._mapping)
    else:
        item = {field: getattr(row, field, None) for field in _FIELDS}

    if "blocks" in item and isinstance(item["blocks"], str):
        item["blocks"] = json.loads(item["blocks"])

    return item


def list_newsletters(
    *,
    db: Session,
    status_filter: str | None,
) -> list[dict[str, Any]]:
    params = {}
    clause = ""

    if status_filter:
        clause = "WHERE status = :status"
        params["status"] = status_filter

    rows = db.execute(
        text(
            f"""
            SELECT {", ".join(_FIELDS)}
            FROM newsletter_drafts
            {clause}
            ORDER BY created_at DESC, id DESC
            """
        ),
        params,
    ).fetchall()

    return [row_dict(row) for row in rows if row is not None]


def get_newsletter(
    *,
    db: Session,
    newsletter_id: int,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            f"""
            SELECT {", ".join(_FIELDS)}
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

    if row is None:
        return None

    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)

    return dict(vars(row))


def create_newsletter(
    *,
    db: Session,
    values: dict[str, Any],
    user: CurrentUser,
) -> dict[str, Any]:
    row = db.execute(
        text(
            f"""
            INSERT INTO newsletter_drafts (
                name,
                subject,
                preview_text,
                status,
                sender_identity_id,
                blocks,
                body_text,
                body_html,
                created_by,
                updated_by,
                created_at,
                updated_at
            )
            VALUES (
                :name,
                :subject,
                :preview_text,
                'draft',
                :sender_identity_id,
                CAST(:blocks AS JSONB),
                :body_text,
                :body_html,
                :created_by,
                :updated_by,
                NOW(),
                NOW()
            )
            RETURNING {", ".join(_FIELDS)}
            """
        ),
        {
            **values,
            "blocks": json.dumps(values["blocks"]),
            "created_by": user.id,
            "updated_by": user.id,
        },
    ).fetchone()

    db.commit()
    return row_dict(row) or {}


def update_newsletter(
    *,
    db: Session,
    newsletter_id: int,
    values: dict[str, Any],
    user: CurrentUser,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            f"""
            UPDATE newsletter_drafts
            SET
                name = :name,
                subject = :subject,
                preview_text = :preview_text,
                sender_identity_id = :sender_identity_id,
                blocks = CAST(:blocks AS JSONB),
                body_text = :body_text,
                body_html = :body_html,
                updated_by = :updated_by,
                updated_at = NOW()
            WHERE id = :newsletter_id
            RETURNING {", ".join(_FIELDS)}
            """
        ),
        {
            **values,
            "blocks": json.dumps(values["blocks"]),
            "newsletter_id": newsletter_id,
            "updated_by": user.id,
        },
    ).fetchone()

    db.commit()
    return row_dict(row)


def mark_in_review(
    *,
    db: Session,
    newsletter_id: int,
    user_id: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE newsletter_drafts
            SET
                status = 'in_review',
                reviewed_by = :user_id,
                reviewed_at = NOW(),
                updated_by = :user_id,
                updated_at = NOW()
            WHERE id = :newsletter_id
            """
        ),
        {"newsletter_id": newsletter_id, "user_id": user_id},
    )


def mark_approved(
    *,
    db: Session,
    newsletter_id: int,
    user_id: int,
) -> None:
    db.execute(
        text(
            """
            UPDATE newsletter_drafts
            SET
                status = 'approved',
                approved_by = :user_id,
                approved_at = NOW(),
                updated_by = :user_id,
                updated_at = NOW()
            WHERE id = :newsletter_id
            """
        ),
        {"newsletter_id": newsletter_id, "user_id": user_id},
    )


def get_leads_by_ids(
    *,
    db: Session,
    lead_ids: list[int],
) -> dict[int, dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT id, name, contact_person, owner_name, email
            FROM leads
            WHERE id = ANY(:lead_ids)
            """
        ),
        {"lead_ids": lead_ids},
    ).fetchall()

    result = {}

    for row in rows:
        item = (
            dict(row._mapping)
            if getattr(row, "_mapping", None) is not None
            else dict(vars(row))
        )
        result[item["id"]] = item

    return result


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


def recipient_exists(
    *,
    db: Session,
    newsletter_id: int,
    lead_id: int,
    email_address: str,
) -> bool:
    row = db.execute(
        text(
            """
            SELECT TRUE AS exists
            FROM newsletter_recipients
            WHERE
                newsletter_id = :newsletter_id
                AND (
                    lead_id = :lead_id
                    OR LOWER(recipient_email) = :email_address
                )
            LIMIT 1
            """
        ),
        {
            "newsletter_id": newsletter_id,
            "lead_id": lead_id,
            "email_address": email_address.strip().lower(),
        },
    ).fetchone()

    return bool(row)


def create_recipient(
    *,
    db: Session,
    newsletter_id: int,
    lead_id: int,
    recipient_email: str,
    recipient_name: str | None,
    company_name: str | None,
    added_by: int,
) -> int:
    row = db.execute(
        text(
            """
            INSERT INTO newsletter_recipients (
                newsletter_id,
                lead_id,
                recipient_email,
                recipient_name,
                company_name_snapshot,
                status,
                added_by,
                created_at,
                updated_at
            )
            VALUES (
                :newsletter_id,
                :lead_id,
                :recipient_email,
                :recipient_name,
                :company_name_snapshot,
                'eligible',
                :added_by,
                NOW(),
                NOW()
            )
            RETURNING id
            """
        ),
        {
            "newsletter_id": newsletter_id,
            "lead_id": lead_id,
            "recipient_email": recipient_email.strip().lower(),
            "recipient_name": recipient_name,
            "company_name_snapshot": company_name,
            "added_by": added_by,
        },
    ).fetchone()

    return int(row.id)


def list_recipients(
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
                status,
                suppression_reason,
                added_by,
                created_at,
                updated_at
            FROM newsletter_recipients
            WHERE newsletter_id = :newsletter_id
            ORDER BY created_at DESC, id DESC
            """
        ),
        {"newsletter_id": newsletter_id},
    ).fetchall()

    return [
        dict(row._mapping)
        if getattr(row, "_mapping", None) is not None
        else dict(vars(row))
        for row in rows
    ]


def create_test_message(
    *,
    db: Session,
    newsletter_id: int,
    sender_identity_id: int,
    sent_by: int,
    recipient_email: str,
    subject: str,
    body_text: str,
    body_html: str,
) -> int:
    row = db.execute(
        text(
            """
            INSERT INTO email_messages (
                newsletter_id,
                sender_identity_id,
                sent_by,
                recipient_email,
                subject,
                body_text,
                body_html,
                message_type,
                status,
                idempotency_key,
                created_at,
                updated_at
            )
            VALUES (
                :newsletter_id,
                :sender_identity_id,
                :sent_by,
                :recipient_email,
                :subject,
                :body_text,
                :body_html,
                'newsletter_test',
                'processing',
                :idempotency_key,
                NOW(),
                NOW()
            )
            RETURNING id
            """
        ),
        {
            "newsletter_id": newsletter_id,
            "sender_identity_id": sender_identity_id,
            "sent_by": sent_by,
            "recipient_email": recipient_email,
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "idempotency_key": (
                f"newsletter-test:{newsletter_id}:"
                f"{recipient_email}:{sent_by}"
            ),
        },
    ).fetchone()

    return int(row.id)


def mark_test_sent(
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
                updated_at = NOW()
            WHERE id = :message_id
            """
        ),
        {
            "message_id": message_id,
            "provider_message_id": provider_message_id,
        },
    )


def suppress_email(
    *,
    db: Session,
    email_address: str,
    reason: str,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO suppression_list (
                email_address,
                reason,
                source,
                is_active,
                created_at,
                updated_at
            )
            VALUES (
                :email_address,
                :reason,
                'newsletter_unsubscribe',
                TRUE,
                NOW(),
                NOW()
            )
            ON CONFLICT (LOWER(email_address))
                WHERE email_address IS NOT NULL
            DO UPDATE SET
                reason = EXCLUDED.reason,
                source = EXCLUDED.source,
                is_active = TRUE,
                updated_at = NOW()
            """
        ),
        {
            "email_address": email_address.strip().lower(),
            "reason": reason,
        },
    )
