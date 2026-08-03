"""Read-only PostgreSQL catalogue audit for Communications schema."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


REQUIRED_TABLES = frozenset(
    {
        "communication_templates",
        "sender_identities",
        "email_messages",
        "email_events",
        "suppression_list",
        "campaigns",
        "campaign_recipients",
        "campaign_steps",
        "automation_rules",
        "automation_executions",
        "lead_communication_state",
        "newsletter_drafts",
        "newsletter_recipients",
        "newsletter_sources",
        "newsletter_articles",
        "newsletter_ingestion_runs",
        "newsletter_generation_runs",
        "newsletter_draft_articles",
    }
)

REQUIRED_COLUMNS = frozenset(
    {
        ("email_messages", "provider_message_id"),
        ("email_messages", "idempotency_key"),
        ("email_messages", "next_attempt_at"),
        ("email_messages", "locked_at"),
        ("email_messages", "locked_by"),
        ("email_messages", "newsletter_recipient_id"),
        ("email_messages", "delivered_at"),
        ("email_messages", "open_count"),
        ("email_messages", "click_count"),
        ("email_events", "provider_event_id"),
        ("email_events", "signature_verified"),
        ("campaigns", "status"),
        ("automation_rules", "is_active"),
        ("newsletter_drafts", "generation_run_id"),
        ("newsletter_drafts", "source_type"),
        ("newsletter_recipients", "status"),
        ("newsletter_articles", "fingerprint"),
    }
)

REQUIRED_INDEXES = frozenset(
    {
        "uq_email_messages_idempotency_key",
        "uq_email_events_provider_event",
        "uq_newsletter_recipients_email",
        "uq_email_messages_newsletter_recipient",
        "uq_newsletter_articles_fingerprint",
        "uq_newsletter_articles_canonical_url",
        "uq_newsletter_draft_articles",
    }
)


def _value(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row.get(name)

    if getattr(row, "_mapping", None) is not None:
        return row._mapping.get(name)

    return getattr(row, name, None)


def inspect_schema(*, db: Session) -> dict[str, Any]:
    table_rows = db.execute(
        text(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        ),
        {},
    ).fetchall()
    column_rows = db.execute(
        text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            """
        ),
        {},
    ).fetchall()
    index_rows = db.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
            """
        ),
        {},
    ).fetchall()

    present_tables = {
        str(_value(row, "table_name"))
        for row in table_rows
    }
    present_columns = {
        (
            str(_value(row, "table_name")),
            str(_value(row, "column_name")),
        )
        for row in column_rows
    }
    present_indexes = {
        str(_value(row, "indexname"))
        for row in index_rows
    }

    missing_tables = sorted(
        REQUIRED_TABLES - present_tables
    )
    missing_columns = sorted(
        f"{table_name}.{column_name}"
        for table_name, column_name in (
            REQUIRED_COLUMNS - present_columns
        )
    )
    missing_indexes = sorted(
        REQUIRED_INDEXES - present_indexes
    )

    return {
        "ready": not (
            missing_tables
            or missing_columns
            or missing_indexes
        ),
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "missing_indexes": missing_indexes,
    }
