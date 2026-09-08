from pathlib import Path


def test_apollo_prospect_migration_defines_required_tables_and_indexes():
    migration = Path(
        "backend/migrations/010_apollo_prospecting.sql"
    )

    assert migration.exists()

    sql = migration.read_text().lower()

    assert "create table if not exists apollo_prospects" in sql
    assert "create table if not exists apollo_prospect_contacts" in sql

    assert "apollo_organization_id" in sql
    assert "normalized_name" in sql
    assert "review_status" in sql
    assert "imported_lead_id" in sql

    assert "prospect_id" in sql
    assert "apollo_person_id" in sql
    assert "enrichment_status" in sql

    assert "references apollo_prospects(id) on delete cascade" in sql
    assert "references leads(id)" in sql

    assert "apollo_prospects_org_id_idx" in sql
    assert "apollo_prospects_domain_idx" in sql
    assert "apollo_prospects_normalized_name_idx" in sql
    assert "apollo_prospect_contacts_prospect_idx" in sql
    assert "apollo_prospect_contacts_person_idx" in sql
