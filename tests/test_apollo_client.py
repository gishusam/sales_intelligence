import httpx
import pytest

from app.services.apollo import (
    ApolloClient,
    ApolloConfigurationError,
)


def test_apollo_client_rejects_missing_api_key():
    with pytest.raises(ApolloConfigurationError):
        ApolloClient(api_key="")


def test_apollo_health_sends_api_key_header():
    captured = {}

    def handler(request: httpx.Request):
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("x-api-key")

        return httpx.Response(
            200,
            json={"is_logged_in": True},
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)

    client = ApolloClient(
        api_key="test-apollo-key",
        http_client=http_client,
    )

    response = client.health()

    assert response == {"is_logged_in": True}
    assert captured["method"] == "GET"
    assert captured["url"] == "https://api.apollo.io/api/v1/auth/health"
    assert captured["api_key"] == "test-apollo-key"


def test_search_organizations_sends_filters_and_authentication():
    captured = {}

    def handler(request: httpx.Request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["api_key"] = request.headers.get("x-api-key")
        captured["locations"] = request.url.params.get_list(
            "organization_locations[]"
        )
        captured["employee_ranges"] = request.url.params.get_list(
            "organization_num_employees_ranges[]"
        )
        captured["keywords"] = request.url.params.get_list(
            "q_organization_keyword_tags[]"
        )
        captured["page"] = request.url.params.get("page")
        captured["per_page"] = request.url.params.get("per_page")

        return httpx.Response(
            200,
            json={
                "organizations": [
                    {
                        "id": "org-123",
                        "name": "Acme Property Management",
                    }
                ],
                "pagination": {
                    "page": 1,
                    "per_page": 25,
                    "total_entries": 1,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)

    client = ApolloClient(
        api_key="test-apollo-key",
        http_client=http_client,
    )

    response = client.search_organizations(
        locations=["Kenya"],
        employee_ranges=["5,200"],
        keywords=[
            "property management",
            "real estate",
        ],
        page=1,
        per_page=25,
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/mixed_companies/search"
    assert captured["api_key"] == "test-apollo-key"
    assert captured["locations"] == ["Kenya"]
    assert captured["employee_ranges"] == ["5,200"]
    assert captured["keywords"] == [
        "property management",
        "real estate",
    ]
    assert captured["page"] == "1"
    assert captured["per_page"] == "25"

    assert response["organizations"][0]["id"] == "org-123"


def test_search_people_sends_decision_maker_filters():
    captured = {}

    def handler(request: httpx.Request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["api_key"] = request.headers.get("x-api-key")
        captured["titles"] = request.url.params.get_list(
            "person_titles[]"
        )
        captured["seniorities"] = request.url.params.get_list(
            "person_seniorities[]"
        )
        captured["organization_ids"] = request.url.params.get_list(
            "organization_ids[]"
        )
        captured["page"] = request.url.params.get("page")
        captured["per_page"] = request.url.params.get("per_page")

        return httpx.Response(
            200,
            json={
                "people": [
                    {
                        "id": "person-123",
                        "name": "Jane Doe",
                        "title": "Managing Director",
                    }
                ],
                "pagination": {
                    "page": 1,
                    "per_page": 25,
                    "total_entries": 1,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)

    client = ApolloClient(
        api_key="test-apollo-key",
        http_client=http_client,
    )

    response = client.search_people(
        organization_ids=["org-123"],
        titles=[
            "Founder",
            "Managing Director",
            "Operations Manager",
        ],
        seniorities=[
            "owner",
            "founder",
            "c_suite",
            "head",
            "director",
            "manager",
        ],
        page=1,
        per_page=25,
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/mixed_people/api_search"
    assert captured["api_key"] == "test-apollo-key"
    assert captured["titles"] == [
        "Founder",
        "Managing Director",
        "Operations Manager",
    ]
    assert captured["seniorities"] == [
        "owner",
        "founder",
        "c_suite",
        "head",
        "director",
        "manager",
    ]
    assert captured["organization_ids"] == ["org-123"]
    assert captured["page"] == "1"
    assert captured["per_page"] == "25"

    assert response["people"][0]["id"] == "person-123"


def test_enrich_person_sends_apollo_person_id():
    import httpx

    from app.services.apollo import ApolloClient

    captured = {}

    def handler(request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["api_key"] = request.headers.get("x-api-key")
        captured["id"] = request.url.params.get("id")
        captured["reveal_personal_emails"] = request.url.params.get(
            "reveal_personal_emails"
        )
        captured["reveal_phone_number"] = request.url.params.get(
            "reveal_phone_number"
        )

        return httpx.Response(
            200,
            json={
                "person": {
                    "id": "person-123",
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "name": "Jane Doe",
                    "email": "jane@example.com",
                    "title": "Managing Director",
                }
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)

    client = ApolloClient(
        api_key="test-apollo-key",
        http_client=http_client,
    )

    response = client.enrich_person(
        person_id="person-123",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/people/match"
    assert captured["api_key"] == "test-apollo-key"
    assert captured["id"] == "person-123"

    assert captured["reveal_personal_emails"] == "false"
    assert captured["reveal_phone_number"] == "false"

    assert response["person"]["id"] == "person-123"
    assert response["person"]["email"] == "jane@example.com"
