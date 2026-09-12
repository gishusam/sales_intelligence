from pathlib import Path


def test_credit_aware_enrichment_migration_adds_resumable_queue_contract():
    sql = Path(
        "backend/migrations/013_apollo_credit_aware_enrichment.sql"
    ).read_text().lower()

    for column in (
        "processed_count",
        "no_contact_count",
        "failed_count",
        "queued_count",
        "credit_status",
        "billing_cycle_reset_at",
        "assigned_to",
        "enrichment_started_at",
        "enrichment_completed_at",
        "updated_at",
        "contact_id",
        "status",
        "attempts",
        "processed_at",
        "last_error",
    ):
        assert column in sql

    assert "unique (search_run_id, prospect_id)" in sql
