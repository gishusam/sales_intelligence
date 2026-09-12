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


def test_enrich_organization_sends_company_identifiers():
    import httpx

    from app.services.apollo import ApolloClient

    captured = {}

    def handler(request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["api_key"] = request.headers.get("x-api-key")
        captured["domain"] = request.url.params.get("domain")
        captured["linkedin_url"] = request.url.params.get(
            "linkedin_url"
        )
        captured["website"] = request.url.params.get("website")
        captured["name"] = request.url.params.get("name")

        return httpx.Response(
            200,
            json={
                "organization": {
                    "id": "5e562b21b0b5190001a53287",
                    "name": "Centum Real Estate",
                    "primary_domain": "centum.co.ke",
                    "estimated_num_employees": 50,
                    "city": "Nairobi",
                    "country": "Kenya",
                    "industry": "real estate",
                }
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)

    client = ApolloClient(
        api_key="test-apollo-key",
        http_client=http_client,
    )

    response = client.enrich_organization(
        domain="centum.co.ke",
        linkedin_url=(
            "https://www.linkedin.com/company/centum-re"
        ),
        website="https://centum.co.ke",
        name="Centum Real Estate",
    )

    assert captured["method"] == "GET"
    assert captured["path"] == "/api/v1/organizations/enrich"
    assert captured["api_key"] == "test-apollo-key"

    assert captured["domain"] == "centum.co.ke"
    assert captured["linkedin_url"] == (
        "https://www.linkedin.com/company/centum-re"
    )
    assert captured["website"] == "https://centum.co.ke"
    assert captured["name"] == "Centum Real Estate"

    assert response["organization"]["name"] == (
        "Centum Real Estate"
    )


def test_enrich_contact_details_requests_native_phone_reveal():
    captured = {}

    def handler(request: httpx.Request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["api_key"] = request.headers.get("x-api-key")

        for key in (
            "id",
            "first_name",
            "last_name",
            "linkedin_url",
            "reveal_personal_emails",
            "reveal_phone_number",
            "run_waterfall_email",
            "run_waterfall_phone",
            "webhook_url",
        ):
            captured[key] = request.url.params.get(key)

        return httpx.Response(
            200,
            json={
                "person": {
                    "id": "68527052713b92000135dac5",
                    "name": "Kenneth Mbae",
                },
                "phone_enrichment": {
                    "status": "pending",
                    "request_id": "phone-request-123",
                },
                "request_id": -123456789,
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)

    client = ApolloClient(
        api_key="test-apollo-key",
        http_client=http_client,
    )

    response = client.enrich_contact_details(
        person_id="68527052713b92000135dac5",
        first_name="Kenneth",
        last_name="Mbae",
        linkedin_url=(
            "http://www.linkedin.com/in/"
            "kenneth-mbae-0b5b17a4"
        ),
        webhook_url=(
            "https://api.example.com/"
            "api/apollo/webhooks/contact-enrichment"
        ),
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/people/match"
    assert captured["api_key"] == "test-apollo-key"

    assert captured["id"] == "68527052713b92000135dac5"
    assert captured["first_name"] == "Kenneth"
    assert captured["last_name"] == "Mbae"

    assert captured["reveal_personal_emails"] == "false"
    assert captured["reveal_phone_number"] == "true"

    assert captured["run_waterfall_email"] is None
    assert captured["run_waterfall_phone"] is None

    assert captured["webhook_url"] == (
        "https://api.example.com/"
        "api/apollo/webhooks/contact-enrichment"
    )

    assert response["phone_enrichment"]["status"] == "pending"
    assert response["request_id"] == -123456789


def test_search_people_supports_discovery_filters():
    import httpx

    from app.services.apollo import ApolloClient

    captured = {}

    def handler(request):
        captured["organization_ids"] = (
            request.url.params.get_list("organization_ids[]")
        )
        captured["person_locations"] = (
            request.url.params.get_list("person_locations[]")
        )
        captured["employee_ranges"] = (
            request.url.params.get_list(
                "organization_num_employees_ranges[]"
            )
        )
        captured["titles"] = (
            request.url.params.get_list("person_titles[]")
        )
        captured["seniorities"] = (
            request.url.params.get_list(
                "person_seniorities[]"
            )
        )

        return httpx.Response(
            200,
            json={
                "people": [],
                "pagination": {},
            },
        )

    client = ApolloClient(
        api_key="test-apollo-key",
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler)
        ),
    )

    client.search_people(
        organization_ids=[],
        titles=[
            "Property Manager",
            "Managing Director",
        ],
        seniorities=[
            "manager",
            "director",
        ],
        person_locations=[
            "Westlands, Nairobi, Kenya",
        ],
        employee_ranges=["5,200"],
        page=1,
        per_page=25,
    )

    assert captured["organization_ids"] == []
    assert captured["person_locations"] == [
        "Westlands, Nairobi, Kenya",
    ]
    assert captured["employee_ranges"] == ["5,200"]
    assert captured["titles"] == [
        "Property Manager",
        "Managing Director",
    ]
    assert captured["seniorities"] == [
        "manager",
        "director",
    ]


def test_search_organizations_can_filter_by_organization_ids():
    import httpx

    from app.services.apollo import ApolloClient

    captured = {}

    def handler(request):
        captured["organization_ids"] = (
            request.url.params.get_list("organization_ids[]")
        )
        captured["locations"] = (
            request.url.params.get_list(
                "organization_locations[]"
            )
        )
        captured["keywords"] = (
            request.url.params.get_list(
                "q_organization_keyword_tags[]"
            )
        )

        return httpx.Response(
            200,
            json={
                "organizations": [],
                "pagination": {},
            },
        )

    client = ApolloClient(
        api_key="test-apollo-key",
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler)
        ),
    )

    client.search_organizations(
        locations=[],
        employee_ranges=["5,200"],
        keywords=["property management"],
        organization_ids=[
            "org-1",
            "org-2",
        ],
        page=1,
        per_page=25,
    )

    assert captured["organization_ids"] == [
        "org-1",
        "org-2",
    ]
    assert captured["locations"] == []
    assert captured["keywords"] == [
        "property management",
    ]
