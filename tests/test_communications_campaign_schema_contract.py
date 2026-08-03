from pathlib import Path


def migration_source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "2026_08_communications.sql"
    ).read_text(encoding="utf-8").lower()


def test_campaign_tables_have_draft_and_enrolment_columns():
    source = migration_source()

    required = {
        "campaign_type",
        "sender_identity_id",
        "step_order",
        "delay_days",
        "template_id",
        "recipient_email",
        "recipient_name",
        "enrolled_by",
        "enrolled_at",
    }

    for column in required:
        assert column in source

    assert "'draft'" in source
    assert "'enrolled'" in source


def test_campaign_steps_and_recipients_are_unique():
    source = migration_source()

    assert "uq_campaign_steps_order" in source
    assert "uq_campaign_recipients_lead" in source
    assert "uq_campaign_recipients_email" in source
