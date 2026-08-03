from pathlib import Path


def source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "2026_08_communications.sql"
    ).read_text(encoding="utf-8").lower()


def test_provider_event_and_engagement_columns_exist():
    migration = source()

    for item in {
        "provider_event_id",
        "signature_verified",
        "occurred_at",
        "delivered_at",
        "bounced_at",
        "complained_at",
        "unsubscribed_at",
        "opened_at",
        "clicked_at",
        "open_count",
        "click_count",
        "bounce_type",
    }:
        assert item in migration

    assert "uq_email_events_provider_event" in migration
