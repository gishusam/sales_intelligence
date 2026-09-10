from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import CurrentUser, get_current_user
from app.config import settings
from app.database import Base, get_db
from app.models.lead import Lead
from app.models.apollo_prospect import (
    ApolloProspect,
    ApolloProspectContact,
)
from app.routers import apollo as apollo_router


def test_enrich_endpoint_enriches_selected_prospect(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(
        engine,
        tables=[
            Lead.__table__,
            ApolloProspect.__table__,
            ApolloProspectContact.__table__,
        ],
    )

    Session = sessionmaker(bind=engine)
    db = Session()

    prospect = ApolloProspect(
        apollo_organization_id="5e562b21b0b5190001a53287",
        name="Centum Real Estate",
        normalized_name="centum real estate",
        linkedin_url=(
            "https://www.linkedin.com/company/centum-re"
        ),
        quality_score=5,
        quality_band="weak",
        review_status="discovered",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="68527052713b92000135dac5",
        first_name="Kenneth",
        name="Kenneth Mb***e",
        title="Managing Director",
        seniority="c_suite",
        enrichment_status="not_enriched",
    )

    db.add(contact)
    db.commit()

    prospect_id = prospect.id

    monkeypatch.setattr(
        settings,
        "APOLLO_API_KEY",
        "test-apollo-key",
    )

    class FakeApolloClient:
        def __init__(self, api_key):
            assert api_key == "test-apollo-key"

        def enrich_organization(
            self,
            *,
            domain=None,
            linkedin_url=None,
            website=None,
            name=None,
        ):
            assert name == "Centum Real Estate"

            return {
                "organization": {
                    "id": "5e562b21b0b5190001a53287",
                    "name": "Centum Real Estate",
                    "primary_domain": "centum.co.ke",
                    "website_url": "https://centum.co.ke",
                    "linkedin_url": (
                        "https://www.linkedin.com/company/"
                        "centum-re"
                    ),
                    "estimated_num_employees": 85,
                    "city": "Nairobi",
                    "country": "Kenya",
                    "industry": "real estate",
                }
            }

        def enrich_person(self, person_id):
            assert person_id == "68527052713b92000135dac5"

            return {
                "person": {
                    "id": person_id,
                    "first_name": "Kenneth",
                    "last_name": "Mbae",
                    "name": "Kenneth Mbae",
                    "title": "Managing Director",
                    "linkedin_url": (
                        "http://www.linkedin.com/in/"
                        "kenneth-mbae-0b5b17a4"
                    ),
                    "email": None,
                }
            }

    monkeypatch.setattr(
        apollo_router,
        "ApolloClient",
        FakeApolloClient,
    )

    def override_get_db():
        try:
            yield db
        finally:
            pass

    def override_get_current_user():
        return CurrentUser(
            id=7,
            name="Jane Sales",
            email="jane@example.com",
            role="sales",
        )

    app = FastAPI()
    app.include_router(apollo_router.router)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[
        get_current_user
    ] = override_get_current_user

    client = TestClient(app)

    response = client.post(
        f"/api/apollo/prospects/{prospect_id}/enrich"
    )

    assert response.status_code == 200

    assert response.json() == {
        "id": prospect_id,
        "review_status": "enriched",
        "quality_score": 75.0,
        "quality_band": "good",
    }

    db.refresh(prospect)
    db.refresh(contact)

    assert prospect.review_status == "enriched"
    assert prospect.domain == "centum.co.ke"
    assert prospect.city == "Nairobi"
    assert prospect.country == "Kenya"
    assert prospect.employee_count == 85

    assert contact.name == "Kenneth Mbae"
    assert contact.title == "Managing Director"
    assert contact.enrichment_status == "enriched"
