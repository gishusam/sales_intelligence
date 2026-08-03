from pathlib import Path


def source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "2026_08_communications.sql"
    ).read_text(encoding="utf-8").lower()


def test_automation_rule_and_execution_columns_exist():
    migration = source()

    required = {
        "trigger_type",
        "inactivity_days",
        "max_drafts_per_lead",
        "automation_rule_id",
        "idempotency_key",
        "reply_received_at",
        "automation_paused",
        "pause_reason",
    }

    for column in required:
        assert column in migration


def test_automation_execution_and_lead_state_are_unique():
    migration = source()

    assert "uq_automation_executions_idempotency_key" in migration
    assert "lead_communication_state" in migration
    assert "primary key" in migration
