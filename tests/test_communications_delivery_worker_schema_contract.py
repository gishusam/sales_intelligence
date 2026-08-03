from pathlib import Path


def source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "2026_08_communications.sql"
    ).read_text(encoding="utf-8").lower()


def test_worker_columns_and_indexes_exist():
    migration = source()

    required = {
        "attempt_count",
        "next_attempt_at",
        "locked_at",
        "locked_by",
        "last_attempt_at",
        "dead_lettered_at",
    }

    for column in required:
        assert column in migration

    assert "idx_email_messages_worker_due" in migration
    assert "idx_email_messages_stale_processing" in migration
