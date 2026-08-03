from pathlib import Path


def migration_source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "2026_08_communications.sql"
    ).read_text(encoding="utf-8").lower()


def test_newsletter_schema_contract():
    source = migration_source()

    for item in {
        "preview_text",
        "sender_identity_id",
        "blocks",
        "body_text",
        "body_html",
        "reviewed_by",
        "approved_by",
        "newsletter_recipients",
        "recipient_email",
        "company_name_snapshot",
        "newsletter_id",
    }:
        assert item in source

    assert "uq_newsletter_recipients_email" in source
    assert "lower(recipient_email)" in source
