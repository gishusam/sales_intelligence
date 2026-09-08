import httpx


APOLLO_BASE_URL = "https://api.apollo.io/api/v1"


class ApolloConfigurationError(ValueError):
    """Raised when Apollo is not configured correctly."""


class ApolloClient:
    def __init__(
        self,
        api_key: str,
        http_client: httpx.Client | None = None,
    ):
        api_key = api_key.strip()

        if not api_key:
            raise ApolloConfigurationError(
                "Apollo API key is required."
            )

        self.api_key = api_key
        self.http_client = http_client or httpx.Client(
            timeout=15.0,
        )

    def health(self) -> dict:
        response = self.http_client.get(
            f"{APOLLO_BASE_URL}/auth/health",
            headers={
                "x-api-key": self.api_key,
            },
        )

        response.raise_for_status()
        return response.json()


    def search_organizations(
        self,
        locations: list[str],
        employee_ranges: list[str],
        keywords: list[str],
        page: int = 1,
        per_page: int = 25,
    ) -> dict:
        params = []

        for location in locations:
            params.append(
                ("organization_locations[]", location)
            )

        for employee_range in employee_ranges:
            params.append(
                (
                    "organization_num_employees_ranges[]",
                    employee_range,
                )
            )

        for keyword in keywords:
            params.append(
                ("q_organization_keyword_tags[]", keyword)
            )

        params.extend(
            [
                ("page", page),
                ("per_page", per_page),
            ]
        )

        response = self.http_client.post(
            f"{APOLLO_BASE_URL}/mixed_companies/search",
            headers={
                "x-api-key": self.api_key,
            },
            params=params,
        )

        response.raise_for_status()
        return response.json()

    def search_people(
        self,
        organization_ids: list[str],
        titles: list[str],
        seniorities: list[str],
        page: int = 1,
        per_page: int = 25,
    ) -> dict:
        params = []

        for title in titles:
            params.append(("person_titles[]", title))

        for seniority in seniorities:
            params.append(
                ("person_seniorities[]", seniority)
            )

        for organization_id in organization_ids:
            params.append(
                ("organization_ids[]", organization_id)
            )

        params.extend(
            [
                ("page", page),
                ("per_page", per_page),
            ]
        )

        response = self.http_client.post(
            f"{APOLLO_BASE_URL}/mixed_people/api_search",
            headers={
                "x-api-key": self.api_key,
            },
            params=params,
        )

        response.raise_for_status()
        return response.json()


    def enrich_person(
        self,
        person_id: str,
    ) -> dict:
        response = self.http_client.post(
            f"{APOLLO_BASE_URL}/people/match",
            headers={
                "x-api-key": self.api_key,
            },
            params={
                "id": person_id,
                "reveal_personal_emails": "false",
                "reveal_phone_number": "false",
            },
        )

        response.raise_for_status()
        return response.json()
