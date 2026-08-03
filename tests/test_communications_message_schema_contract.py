from pathlib import Path


def source():
    return (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "2026_08_communications.sql"
    ).read_text(encoding="utf-8").lower()


def test_message_send_is_auditable_and_idempotent():
    migration = source()
    for column in (
        "sender_identity_id",
        "recipient_email",
        "body_text",
        "idempotency_key",
        "provider_message_id",
        "error_message",
        "sent_at",
    ):
        assert column in migration

    assert "uq_email_messages_idempotency_key" in migration
    assert "lower(email_address)" in migration
    assert "uq_suppression_list_normalized_email" in migration
