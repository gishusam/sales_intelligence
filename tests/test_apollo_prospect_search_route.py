from app.config import settings
from app.routers import apollo as apollo_router
from app.schemas.apollo import ProspectSearchRequest


class NoopDb:
    def commit(self):
        pass


def test_search_prospects_returns_normalized_organizations(monkeypatch):
    monkeypatch.setattr(
        settings,
        "APOLLO_API_KEY",
        "test-apollo-key",
    )

    captured = {}

    class FakeApolloClient:
        def __init__(self, api_key):
            captured["api_key"] = api_key

        def search_organizations(
            self,
            locations,
            employee_ranges,
            keywords,
            page,
            per_page,
        ):
            captured["locations"] = locations
            captured["employee_ranges"] = employee_ranges
            captured["keywords"] = keywords
            captured["page"] = page
            captured["per_page"] = per_page

            return {
                "organizations": [
                    {
                        "id": "org-123",
                        "name": "Acme Property Management",
                        "primary_domain": "acme.co.ke",
                        "website_url": "https://acme.co.ke",
                        "linkedin_url": "https://linkedin.com/company/acme",
                        "estimated_num_employees": 48,
                        "city": "Nairobi",
                        "country": "Kenya",
                        "industry": "real estate",
                    }
                ],
                "pagination": {
                    "page": 1,
                    "per_page": 25,
                    "total_entries": 1,
                },
            }

    monkeypatch.setattr(
        apollo_router,
        "ApolloClient",
        FakeApolloClient,
    )

    request = ProspectSearchRequest(
        locations=["Kenya"],
        business_types=["property management"],
        employee_min=5,
        employee_max=200,
    )

    monkeypatch.setattr(
        apollo_router,
        "persist_discovered_prospect",
        lambda db, prospect: None,
    )

    response = apollo_router.search_prospects(
        request,
        db=NoopDb(),
    )

    assert captured == {
        "api_key": "test-apollo-key",
        "locations": ["Kenya"],
        "employee_ranges": ["5,200"],
        "keywords": ["property management"],
        "page": 1,
        "per_page": 25,
    }

    prospect = response["prospects"][0]

    assert prospect["apollo_organization_id"] == "org-123"
    assert prospect["name"] == "Acme Property Management"
    assert prospect["domain"] == "acme.co.ke"
    assert prospect["website_url"] == "https://acme.co.ke"
    assert prospect["linkedin_url"] == "https://linkedin.com/company/acme"
    assert prospect["employee_count"] == 48
    assert prospect["city"] == "Nairobi"
    assert prospect["country"] == "Kenya"
    assert prospect["industry"] == "real estate"

    assert response["pagination"] == {
        "page": 1,
        "per_page": 25,
        "total_entries": 1,
    }


def test_search_prospects_attaches_decision_makers(monkeypatch):
    monkeypatch.setattr(
        settings,
        "APOLLO_API_KEY",
        "test-apollo-key",
    )

    captured = {}

    class FakeApolloClient:
        def __init__(self, api_key):
            captured["api_key"] = api_key

        def search_organizations(
            self,
            locations,
            employee_ranges,
            keywords,
            page,
            per_page,
        ):
            return {
                "organizations": [
                    {
                        "id": "org-123",
                        "name": "Acme Property Management",
                        "primary_domain": "acme.co.ke",
                        "website_url": "https://acme.co.ke",
                        "linkedin_url": "https://linkedin.com/company/acme",
                        "estimated_num_employees": 48,
                        "city": "Nairobi",
                        "country": "Kenya",
                        "industry": "real estate",
                    }
                ],
                "pagination": {
                    "page": 1,
                    "per_page": 25,
                    "total_entries": 1,
                },
            }

        def search_people(
            self,
            organization_ids,
            titles,
            seniorities,
            page,
            per_page,
        ):
            captured["organization_ids"] = organization_ids
            captured["titles"] = titles
            captured["seniorities"] = seniorities

            return {
                "people": [
                    {
                        "id": "person-123",
                        "organization_id": "org-123",
                        "first_name": "Jane",
                        "last_name": "Doe",
                        "name": "Jane Doe",
                        "title": "Managing Director",
                        "seniority": "c_suite",
                        "linkedin_url": "https://linkedin.com/in/janedoe",
                    }
                ],
                "pagination": {
                    "page": 1,
                    "per_page": 25,
                    "total_entries": 1,
                },
            }

    monkeypatch.setattr(
        apollo_router,
        "ApolloClient",
        FakeApolloClient,
    )

    request = ProspectSearchRequest(
        locations=["Kenya"],
        business_types=["property management"],
        employee_min=5,
        employee_max=200,
        decision_maker_titles=["Managing Director"],
        decision_maker_seniorities=["c_suite"],
    )

    monkeypatch.setattr(
        apollo_router,
        "persist_discovered_prospect",
        lambda db, prospect: None,
    )

    response = apollo_router.search_prospects(
        request,
        db=NoopDb(),
    )

    assert captured["organization_ids"] == ["org-123"]
    assert captured["titles"] == ["Managing Director"]
    assert captured["seniorities"] == ["c_suite"]

    assert response["prospects"][0]["decision_makers"] == [
        {
            "apollo_person_id": "person-123",
            "apollo_organization_id": "org-123",
            "first_name": "Jane",
            "last_name": "Doe",
            "name": "Jane Doe",
            "title": "Managing Director",
            "seniority": "c_suite",
            "linkedin_url": "https://linkedin.com/in/janedoe",
        }
    ]


def test_search_prospects_returns_quality_scoring(monkeypatch):
    monkeypatch.setattr(
        settings,
        "APOLLO_API_KEY",
        "test-apollo-key",
    )

    class FakeApolloClient:
        def __init__(self, api_key):
            self.api_key = api_key

        def search_organizations(
            self,
            locations,
            employee_ranges,
            keywords,
            page,
            per_page,
        ):
            return {
                "organizations": [
                    {
                        "id": "org-high",
                        "name": "Acme Property Management",
                        "primary_domain": "acme.co.ke",
                        "linkedin_url": "https://linkedin.com/company/acme",
                        "estimated_num_employees": 48,
                        "city": "Nairobi",
                        "country": "Kenya",
                        "industry": "property management",
                        "keywords": [
                            "tenant management",
                            "rent collection",
                            "rental management",
                            "letting",
                            "property portfolio",
                        ],
                    }
                ],
                "pagination": {
                    "page": 1,
                    "per_page": 25,
                    "total_entries": 1,
                },
            }

        def search_people(
            self,
            organization_ids,
            titles,
            seniorities,
            page,
            per_page,
        ):
            return {
                "people": [
                    {
                        "id": "person-high",
                        "organization_id": "org-high",
                        "first_name": "Jane",
                        "last_name": "Doe",
                        "name": "Jane Doe",
                        "title": "Managing Director",
                        "seniority": "c_suite",
                        "linkedin_url": "https://linkedin.com/in/janedoe",
                    }
                ],
                "pagination": {},
            }

    monkeypatch.setattr(
        apollo_router,
        "ApolloClient",
        FakeApolloClient,
    )

    request = ProspectSearchRequest(
        locations=["Kenya"],
        business_types=["property management"],
        employee_min=5,
        employee_max=200,
        decision_maker_titles=["Managing Director"],
        decision_maker_seniorities=["c_suite"],
    )

    monkeypatch.setattr(
        apollo_router,
        "persist_discovered_prospect",
        lambda db, prospect: None,
    )

    response = apollo_router.search_prospects(
        request,
        db=NoopDb(),
    )
    prospect = response["prospects"][0]

    assert prospect["quality_score"] == 100
    assert prospect["quality_band"] == "high"
    assert prospect["score_breakdown"] == {
        "company_fit": 40,
        "decision_maker_fit": 35,
        "market_fit": 15,
        "data_confidence": 10,
    }
    assert prospect["score_reasons"]
