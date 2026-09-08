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
from app.routers import leads as leads_router


def test_imported_apollo_prospect_appears_in_my_leads():
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
        apollo_organization_id="org-my-leads-integration",
        name="Nairobi Prime Property Management",
        normalized_name="nairobi prime property management",
        domain="nairobiprime.co.ke",
        website_url="https://nairobiprime.co.ke",
        city="Nairobi",
        quality_score=93,
        quality_band="high",
        review_status="approved",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-my-leads-integration",
        name="Alice Manager",
        title="Managing Director",
        email="alice@nairobiprime.co.ke",
        phone="+254711111111",
        enrichment_status="enriched",
    )

    db.add(contact)
    db.commit()

    prospect_id = prospect.id

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
    app.include_router(leads_router.router)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(app)

    import_response = client.post(
        f"/api/apollo/prospects/{prospect_id}/import"
    )

    assert import_response.status_code == 200

    my_leads_response = client.get("/api/leads/mine")

    assert my_leads_response.status_code == 200

    payload = my_leads_response.json()

    assert payload["total"] == 1
    assert len(payload["data"]) == 1

    lead = payload["data"][0]

    assert lead["name"] == "Nairobi Prime Property Management"
    assert lead["assigned_to"] == "Jane Sales"
    assert lead["source"] == "apollo"
    assert lead["lead_type"] == "agency"
    assert lead["status"] == "new"
    assert lead["area"] == "Nairobi"
    assert lead["score"] == 93
    assert lead["email"] == "alice@nairobiprime.co.ke"
    assert lead["phone"] == "+254711111111"
