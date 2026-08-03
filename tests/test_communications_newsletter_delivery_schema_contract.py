from pathlib import Path


def source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "2026_08_communications.sql"
    ).read_text(encoding="utf-8").lower()


def test_newsletter_delivery_schema_contract():
    migration = source()

    for item in {
        "scheduled_for",
        "started_at",
        "sent_at",
        "cancelled_at",
        "queued_at",
        "failed_at",
        "newsletter_recipient_id",
    }:
        assert item in migration

    assert "uq_email_messages_newsletter_recipient" in migration
