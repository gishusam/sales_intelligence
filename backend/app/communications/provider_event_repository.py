"""Idempotent email-provider event persistence and reconciliation."""

import json
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


def find_event(
    *,
    db: Session,
    provider: str,
    provider_event_id: str,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT
                id,
                provider,
                provider_event_id,
                email_message_id,
                event_type,
                status
            FROM email_events
            WHERE
                provider = :provider
                AND provider_event_id = :provider_event_id
            """
        ),
        {
            "provider": provider,
            "provider_event_id": provider_event_id,
        },
    ).fetchone()

    return row_dict(row)


def find_message_by_provider_id(
    *,
    db: Session,
    provider_message_id: str,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT
                id,
                provider_message_id,
                campaign_id,
                campaign_recipient_id,
                newsletter_id,
                newsletter_recipient_id,
                recipient_email,
                message_type,
                status
            FROM email_messages
            WHERE provider_message_id = :provider_message_id
            ORDER BY id DESC
            LIMIT 1
            """
        ),
        {"provider_message_id": provider_message_id},
    ).fetchone()

    return row_dict(row)


def create_event(
    *,
    db: Session,
    provider: str,
    provider_event_id: str,
    email_message_id: int | None,
    provider_message_id: str,
    event_type: str,
    recipient_email: str | None,
    bounce_type: str | None,
    reason: str | None,
    url: str | None,
    occurred_at,
    payload: dict[str, Any],
    signature_verified: bool,
    status: str,
) -> int:
    row = db.execute(
        text(
            """
            INSERT INTO email_events (
                provider,
                provider_event_id,
                email_message_id,
                provider_message_id,
                event_type,
                recipient_email,
                bounce_type,
                reason,
                url,
                occurred_at,
                payload,
                signature_verified,
                status,
                created_at
            )
            VALUES (
                :provider,
                :provider_event_id,
                :email_message_id,
                :provider_message_id,
                :event_type,
                :recipient_email,
                :bounce_type,
                :reason,
                :url,
                :occurred_at,
                CAST(:payload AS JSONB),
                :signature_verified,
                :status,
                NOW()
            )
            RETURNING id
            """
        ),
        {
            "provider": provider,
            "provider_event_id": provider_event_id,
            "email_message_id": email_message_id,
            "provider_message_id": provider_message_id,
            "event_type": event_type,
            "recipient_email": recipient_email,
            "bounce_type": bounce_type,
            "reason": reason,
            "url": url,
            "occurred_at": occurred_at,
            "payload": json.dumps(payload, default=str),
            "signature_verified": signature_verified,
            "status": status,
        },
    ).fetchone()

    return int(row.id)


def apply_message_event(
    *,
    db: Session,
    message_id: int,
    event_type: str,
    occurred_at,
    bounce_type: str | None,
    reason: str | None,
) -> None:
    statements = {
        "delivered": """
            UPDATE email_messages
            SET
                status = 'delivered',
                delivered_at = COALESCE(delivered_at, :occurred_at),
                updated_at = NOW()
            WHERE id = :message_id
        """,
        "hard_bounce": """
            UPDATE email_messages
            SET
                status = 'bounced',
                bounced_at = COALESCE(bounced_at, :occurred_at),
                bounce_type = COALESCE(:bounce_type, 'hard'),
                error_message = COALESCE(:reason, error_message),
                updated_at = NOW()
            WHERE id = :message_id
        """,
        "soft_bounce": """
            UPDATE email_messages
            SET
                status = 'bounced',
                bounced_at = COALESCE(bounced_at, :occurred_at),
                bounce_type = COALESCE(:bounce_type, 'soft'),
                error_message = COALESCE(:reason, error_message),
                updated_at = NOW()
            WHERE id = :message_id
        """,
        "complaint": """
            UPDATE email_messages
            SET
                status = 'complained',
                complained_at = COALESCE(complained_at, :occurred_at),
                error_message = COALESCE(:reason, error_message),
                updated_at = NOW()
            WHERE id = :message_id
        """,
        "unsubscribe": """
            UPDATE email_messages
            SET
                status = 'unsubscribed',
                unsubscribed_at = COALESCE(unsubscribed_at, :occurred_at),
                updated_at = NOW()
            WHERE id = :message_id
        """,
        "opened": """
            UPDATE email_messages
            SET
                opened_at = COALESCE(opened_at, :occurred_at),
                open_count = COALESCE(open_count, 0) + 1,
                updated_at = NOW()
            WHERE id = :message_id
        """,
        "clicked": """
            UPDATE email_messages
            SET
                clicked_at = COALESCE(clicked_at, :occurred_at),
                click_count = COALESCE(click_count, 0) + 1,
                updated_at = NOW()
            WHERE id = :message_id
        """,
    }

    db.execute(
        text(statements[event_type]),
        {
            "message_id": message_id,
            "occurred_at": occurred_at,
            "bounce_type": bounce_type,
            "reason": reason,
        },
    )


def update_recipient_from_event(
    *,
    db: Session,
    message: dict[str, Any],
    event_type: str,
    occurred_at,
    reason: str | None,
) -> None:
    status_by_event = {
        "delivered": "delivered",
        "hard_bounce": "bounced",
        "soft_bounce": "bounced",
        "complaint": "complained",
        "unsubscribe": "unsubscribed",
    }
    recipient_status = status_by_event.get(event_type)

    if recipient_status is None:
        return

    if message.get("campaign_recipient_id"):
        db.execute(
            text(
                """
                UPDATE campaign_recipients
                SET
                    status = :status,
                    suppression_reason = CASE
                        WHEN :reason IS NOT NULL
                        THEN :reason
                        ELSE suppression_reason
                    END,
                    updated_at = NOW()
                WHERE id = :recipient_id
                """
            ),
            {
                "recipient_id": message["campaign_recipient_id"],
                "status": recipient_status,
                "reason": reason,
            },
        )

    if message.get("newsletter_recipient_id"):
        db.execute(
            text(
                """
                UPDATE newsletter_recipients
                SET
                    status = :status,
                    suppression_reason = CASE
                        WHEN :reason IS NOT NULL
                        THEN :reason
                        ELSE suppression_reason
                    END,
                    updated_at = NOW()
                WHERE id = :recipient_id
                """
            ),
            {
                "recipient_id": message["newsletter_recipient_id"],
                "status": recipient_status,
                "reason": reason,
            },
        )


def suppress_email(
    *,
    db: Session,
    email_address: str,
    reason: str,
    source: str,
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
                :source,
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
            "source": source,
        },
    )


def list_events(
    *,
    db: Session,
    event_type: str | None,
    message_id: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    clauses = []
    params: dict[str, Any] = {"limit": limit}

    if event_type:
        clauses.append("event_type = :event_type")
        params["event_type"] = event_type

    if message_id:
        clauses.append("email_message_id = :message_id")
        params["message_id"] = message_id

    where_clause = (
        "WHERE " + " AND ".join(clauses)
        if clauses
        else ""
    )

    rows = db.execute(
        text(
            f"""
            SELECT
                id,
                provider,
                provider_event_id,
                email_message_id,
                provider_message_id,
                event_type,
                recipient_email,
                bounce_type,
                reason,
                url,
                occurred_at,
                signature_verified,
                status,
                created_at
            FROM email_events
            {where_clause}
            ORDER BY occurred_at DESC, id DESC
            LIMIT :limit
            """
        ),
        params,
    ).fetchall()

    return [
        row_dict(row)
        for row in rows
        if row is not None
    ]
