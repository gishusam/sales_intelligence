from app.models.apollo_prospect import (
    ApolloProspect,
    ApolloProspectContact,
)


def test_apollo_prospect_model_has_persistence_contract():
    table = ApolloProspect.__table__

    assert table.name == "apollo_prospects"

    expected_columns = {
        "id",
        "apollo_organization_id",
        "name",
        "normalized_name",
        "domain",
        "website_url",
        "linkedin_url",
        "employee_count",
        "city",
        "country",
        "industry",
        "keywords",
        "quality_score",
        "quality_band",
        "score_breakdown",
        "score_reasons",
        "review_status",
        "imported_lead_id",
        "first_seen_at",
        "last_seen_at",
        "created_at",
        "updated_at",
    }

    assert expected_columns.issubset(table.columns.keys())
    assert table.c.review_status.default.arg == "discovered"


def test_apollo_prospect_contact_model_has_persistence_contract():
    table = ApolloProspectContact.__table__

    assert table.name == "apollo_prospect_contacts"

    expected_columns = {
        "id",
        "prospect_id",
        "apollo_person_id",
        "first_name",
        "last_name",
        "name",
        "title",
        "seniority",
        "linkedin_url",
        "email",
        "phone",
        "enrichment_status",
        "created_at",
        "updated_at",
    }

    assert expected_columns.issubset(table.columns.keys())
    assert table.c.enrichment_status.default.arg == "not_enriched"
