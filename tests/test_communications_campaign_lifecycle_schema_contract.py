from pathlib import Path


def source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "2026_08_communications.sql"
    ).read_text(encoding="utf-8").lower()


def test_campaign_lifecycle_columns_are_present():
    migration = source()

    required = {
        "prepared_at",
        "scheduled_for",
        "started_at",
        "paused_at",
        "cancelled_at",
        "paused_from_status",
        "snapshot_subject",
        "snapshot_body_text",
        "snapshot_template_version",
        "company_name_snapshot",
        "area_snapshot",
        "rep_name_snapshot",
        "rep_email_snapshot",
        "campaign_recipient_id",
        "campaign_step_id",
    }

    for column in required:
        assert column in migration


def test_campaign_job_identity_is_unique():
    migration = source()

    assert "uq_email_messages_campaign_recipient_step" in migration
    assert "campaign_recipient_id" in migration
    assert "campaign_step_id" in migration
