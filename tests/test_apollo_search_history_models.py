from app.models.apollo_search_run import (
    ApolloSearchRun,
    ApolloSearchRunProspect,
)


def test_apollo_search_run_model_has_history_contract():
    table = ApolloSearchRun.__table__

    assert table.name == "apollo_search_runs"

    expected_columns = {
        "id",
        "filters",
        "found_count",
        "qualified_count",
        "approved_count",
        "rejected_count",
        "imported_count",
        "credits_used",
        "status",
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
        "created_at",
    }

    assert expected_columns.issubset(table.columns.keys())


def test_apollo_search_run_prospect_links_run_to_prospect():
    table = ApolloSearchRunProspect.__table__

    assert table.name == "apollo_search_run_prospects"

    expected_columns = {
        "id",
        "search_run_id",
        "prospect_id",
        "contact_id",
        "status",
        "attempts",
        "processed_at",
        "last_error",
        "created_at",
    }

    assert expected_columns.issubset(table.columns.keys())

    foreign_keys = {
        str(foreign_key.column)
        for foreign_key in table.foreign_keys
    }

    assert "apollo_search_runs.id" in foreign_keys
    assert "apollo_prospects.id" in foreign_keys
    assert "apollo_prospect_contacts.id" in foreign_keys

    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("search_run_id", "prospect_id") in unique_columns
