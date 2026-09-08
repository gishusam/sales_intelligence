from pathlib import Path


def test_apollo_search_history_migration_defines_tables_and_indexes():
    migration = Path(
        "backend/migrations/011_apollo_search_history.sql"
    )

    assert migration.exists()

    sql = migration.read_text().lower()

    assert "create table if not exists apollo_search_runs" in sql
    assert "create table if not exists apollo_search_run_prospects" in sql

    assert "filters" in sql
    assert "found_count" in sql
    assert "qualified_count" in sql
    assert "approved_count" in sql
    assert "rejected_count" in sql
    assert "imported_count" in sql
    assert "credits_used" in sql

    assert "search_run_id" in sql
    assert "prospect_id" in sql

    assert "references apollo_search_runs(id) on delete cascade" in sql
    assert "references apollo_prospects(id) on delete cascade" in sql

    assert "apollo_search_run_prospects_run_idx" in sql
    assert "apollo_search_run_prospects_prospect_idx" in sql
