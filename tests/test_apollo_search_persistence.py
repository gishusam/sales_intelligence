from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import CurrentUser, get_current_user
from app.database import Base, get_db
from app.models.lead import Lead
from app.models.apollo_prospect import (
    ApolloProspect,
    ApolloProspectContact,
)
from app.routers import apollo as apollo_router


def test_search_persists_returned_prospect_as_discovered(monkeypatch):
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

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app = FastAPI()
    app.include_router(apollo_router.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=7,
        name="Jane Sales",
        email="jane@example.com",
        role="sales",
    )

    monkeypatch.setattr(
        apollo_router.settings,
        "APOLLO_API_KEY",
        "test-apollo-key",
    )

    monkeypatch.setattr(
        apollo_router.ApolloClient,
        "search_organizations",
        lambda self, **kwargs: {
            "organizations": [
                {
                    "id": "org-700",
                    "name": "Nairobi Property Managers",
                    "primary_domain": "npm.co.ke",
                    "website_url": "https://npm.co.ke",
                    "linkedin_url": None,
                    "estimated_num_employees": 25,
                    "city": "Nairobi",
                    "country": "Kenya",
                    "industry": "property management",
                    "keywords": ["tenant management"],
                }
            ],
            "pagination": {
                "page": 1,
                "per_page": 25,
                "total_entries": 1,
            },
        },
    )

    client = TestClient(app)

    response = client.post(
        "/api/apollo/prospects/search",
        json={
            "locations": ["Kenya"],
            "business_types": ["property management"],
            "employee_min": 10,
            "employee_max": 200,
            "decision_maker_titles": [],
            "decision_maker_seniorities": [],
        },
    )

    assert response.status_code == 200

    saved = db.query(ApolloProspect).one()

    returned = response.json()["prospects"][0]

    assert returned["id"] == saved.id
    assert returned["review_status"] == "discovered"

    assert saved.apollo_organization_id == "org-700"
    assert saved.name == "Nairobi Property Managers"
    assert saved.review_status == "discovered"
    assert saved.quality_score > 0
