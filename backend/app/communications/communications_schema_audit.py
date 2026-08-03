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
        ("email_messages", "recipient_email"),
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

REQUIRED_NULLABLE_COLUMNS = frozenset(
    {
        ("email_messages", "from_name"),
        ("email_messages", "from_email"),
        ("email_messages", "to_email"),
        ("email_events", "email_message_id"),
        ("newsletter_drafts", "title"),
        ("automation_executions", "automation_rule_id"),
        ("suppression_list", "email"),
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

REQUIRED_CHECK_CONSTRAINT_TOKENS = {
    "email_message_type_check": {
        "campaign",
        "automation",
        "newsletter_test",
    },
    "email_message_status_check": {
        "processing",
        "dead_letter",
        "delivered",
        "bounced",
        "complained",
        "unsubscribed",
    },
    "campaign_status_check": {
        "ready",
        "scheduled",
        "running",
    },
    "campaign_recipient_status_check": {
        "enrolled",
        "delivered",
        "bounced",
        "complained",
    },
    "newsletter_draft_status_check": {
        "in_review",
        "sending",
        "cancelled",
    },
    "newsletter_source_type_check": {
        "api",
        "n8n",
    },
}


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
            SELECT
                table_name,
                column_name,
                is_nullable
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
    constraint_rows = db.execute(
        text(
            """
            SELECT
                constraint_name,
                check_clause
            FROM information_schema.check_constraints
            WHERE constraint_schema = 'public'
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
    nullable_columns = {
        (
            str(_value(row, "table_name")),
            str(_value(row, "column_name")),
        )
        for row in column_rows
        if str(_value(row, "is_nullable")).upper() == "YES"
    }
    present_indexes = {
        str(_value(row, "indexname"))
        for row in index_rows
    }
    check_constraints = {
        str(_value(row, "constraint_name")): str(
            _value(row, "check_clause") or ""
        ).lower()
        for row in constraint_rows
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

    mismatched_constraints = sorted(
        [
            *(
                f"{table_name}.{column_name} must be nullable"
                for table_name, column_name in (
                    REQUIRED_NULLABLE_COLUMNS
                    - nullable_columns
                )
            ),
            *(
                (
                    f"{constraint_name} must include "
                    f"{', '.join(sorted(tokens))}"
                )
                for constraint_name, tokens
                in REQUIRED_CHECK_CONSTRAINT_TOKENS.items()
                if (
                    constraint_name not in check_constraints
                    or any(
                        token not in check_constraints[constraint_name]
                        for token in tokens
                    )
                )
            ),
        ]
    )

    return {
        "ready": not (
            missing_tables
            or missing_columns
            or missing_indexes
            or mismatched_constraints
        ),
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "missing_indexes": missing_indexes,
        "mismatched_constraints": mismatched_constraints,
    }
