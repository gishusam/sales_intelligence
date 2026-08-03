"""Operational Communications overview totals."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


_FIELDS = (
    "total_messages",
    "queued_messages",
    "processing_messages",
    "sent_messages",
    "delivered_messages",
    "failed_messages",
    "dead_letter_messages",
    "bounced_messages",
    "complained_messages",
    "unsubscribed_messages",
    "opens",
    "clicks",
    "active_campaigns",
    "active_newsletters",
    "suppressed_contacts",
)


def get_overview(*, db: Session) -> dict[str, int]:
    row = db.execute(
        text(
            """
            SELECT
                (
                    SELECT COUNT(*)
                    FROM email_messages
                ) AS total_messages,
                (
                    SELECT COUNT(*)
                    FROM email_messages
                    WHERE status = 'queued'
                ) AS queued_messages,
                (
                    SELECT COUNT(*)
                    FROM email_messages
                    WHERE status = 'processing'
                ) AS processing_messages,
                (
                    SELECT COUNT(*)
                    FROM email_messages
                    WHERE status = 'sent'
                ) AS sent_messages,
                (
                    SELECT COUNT(*)
                    FROM email_messages
                    WHERE status = 'delivered'
                ) AS delivered_messages,
                (
                    SELECT COUNT(*)
                    FROM email_messages
                    WHERE status = 'failed'
                ) AS failed_messages,
                (
                    SELECT COUNT(*)
                    FROM email_messages
                    WHERE status = 'dead_letter'
                ) AS dead_letter_messages,
                (
                    SELECT COUNT(*)
                    FROM email_messages
                    WHERE status = 'bounced'
                ) AS bounced_messages,
                (
                    SELECT COUNT(*)
                    FROM email_messages
                    WHERE status = 'complained'
                ) AS complained_messages,
                (
                    SELECT COUNT(*)
                    FROM email_messages
                    WHERE status = 'unsubscribed'
                ) AS unsubscribed_messages,
                (
                    SELECT COALESCE(SUM(open_count), 0)
                    FROM email_messages
                ) AS opens,
                (
                    SELECT COALESCE(SUM(click_count), 0)
                    FROM email_messages
                ) AS clicks,
                (
                    SELECT COUNT(*)
                    FROM campaigns
                    WHERE status IN (
                        'ready',
                        'scheduled',
                        'running',
                        'paused'
                    )
                ) AS active_campaigns,
                (
                    SELECT COUNT(*)
                    FROM newsletter_drafts
                    WHERE status IN (
                        'approved',
                        'scheduled',
                        'sending'
                    )
                ) AS active_newsletters,
                (
                    SELECT COUNT(*)
                    FROM suppression_list
                    WHERE is_active = TRUE
                ) AS suppressed_contacts
            """
        ),
        {},
    ).fetchone()

    if row is None:
        return {field: 0 for field in _FIELDS}

    if getattr(row, "_mapping", None) is not None:
        values: dict[str, Any] = dict(row._mapping)
    else:
        values = {
            field: getattr(row, field, 0)
            for field in _FIELDS
        }

    return {
        field: int(values.get(field) or 0)
        for field in _FIELDS
    }
