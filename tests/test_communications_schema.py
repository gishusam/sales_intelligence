import re
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "2026_08_communications.sql"
)

REQUIRED_TABLES = {
    "sender_identities",
    "communication_templates",
    "campaigns",
    "campaign_steps",
    "campaign_recipients",
    "email_messages",
    "email_events",
    "audience_segments",
    "automation_rules",
    "automation_executions",
    "suppression_list",
    "newsletter_sources",
    "newsletter_drafts",
}


def test_communications_migration_defines_required_tables():
    assert MIGRATION.exists(), (
        "Create migrations/2026_08_communications.sql"
    )

    sql = MIGRATION.read_text(encoding="utf-8").lower()

    created_tables = set(
        re.findall(
            r"create\s+table\s+"
            r"(?:if\s+not\s+exists\s+)?"
            r"([a-z_][a-z0-9_]*)",
            sql,
        )
    )

    missing = REQUIRED_TABLES - created_tables

    assert not missing, (
        f"Communications migration is missing tables: "
        f"{sorted(missing)}"
    )
