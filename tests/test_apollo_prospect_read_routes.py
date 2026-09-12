from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import CurrentUser, get_current_user
from app.database import Base, get_db
from app.models.apollo_prospect import (
    ApolloProspect,
    ApolloProspectContact,
)
from app.models.lead import Lead
from app.routers import apollo as apollo_router


def make_app_and_db():
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
        yield db

    def override_get_current_user():
        return CurrentUser(
            id=2,
            name="Samuel Ngugi",
            email="samwelngugi24@gmail.com",
            role="sales",
        )

    app = FastAPI()
    app.include_router(apollo_router.router)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[
        get_current_user
    ] = override_get_current_user

    return app, db


def test_list_persisted_prospects_can_filter_by_status():
    app, db = make_app_and_db()

    discovered = ApolloProspect(
        apollo_organization_id="org-discovered",
        name="Westlands Property Managers",
        normalized_name="westlands property managers",
        domain="westlands.example.com",
        city="Nairobi",
        country="Kenya",
        industry="real estate",
        quality_score=71,
        quality_band="good",
        review_status="discovered",
    )

    enriched = ApolloProspect(
        apollo_organization_id="org-enriched",
        name="Nairobi Property Group",
        normalized_name="nairobi property group",
        quality_score=82,
        quality_band="high",
        review_status="enriched",
    )

    db.add_all([discovered, enriched])
    db.flush()

    ready_contact = ApolloProspectContact(
        prospect_id=enriched.id,
        apollo_person_id="person-ready",
        name="Jane Manager",
        title="Property Manager",
        email="jane@example.com",
        phone="+254700000000",
        enrichment_status="enriched",
        contact_enrichment_status="complete",
    )

    db.add(ready_contact)
    db.commit()

    client = TestClient(app)

    response = client.get(
        "/api/apollo/prospects",
        params={"status": "enriched"},
    )

    assert response.status_code == 200

    body = response.json()

    assert len(body["prospects"]) == 1

    prospect = body["prospects"][0]

    assert prospect["id"] == enriched.id
    assert prospect["name"] == "Nairobi Property Group"
    assert prospect["quality_score"] == 82
    assert prospect["quality_band"] == "high"
    assert prospect["review_status"] == "enriched"
    assert prospect["contact_ready"] is True


def test_get_persisted_prospect_returns_contacts():
    app, db = make_app_and_db()

    prospect = ApolloProspect(
        apollo_organization_id="org-detail",
        name="Regent Management Limited",
        normalized_name="regent management limited",
        domain="regent-mgt.com",
        website_url="https://regent-mgt.com",
        linkedin_url="https://linkedin.example/regent",
        employee_count=50,
        city="Nairobi",
        country="Kenya",
        industry="real estate",
        quality_score=78,
        quality_band="good",
        review_status="enriched",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-candy",
        first_name="Candy",
        last_name="Otundo",
        name="Candy Otundo",
        title="Property Manager",
        seniority="manager",
        linkedin_url="https://linkedin.example/candy",
        email="candy@example.com",
        phone="+254711111111",
        enrichment_status="enriched",
        contact_enrichment_status="complete",
        contact_enrichment_request_id="request-123",
    )

    db.add(contact)
    db.commit()

    client = TestClient(app)

    response = client.get(
        f"/api/apollo/prospects/{prospect.id}"
    )

    assert response.status_code == 200

    body = response.json()

    assert body["id"] == prospect.id
    assert body["name"] == "Regent Management Limited"
    assert body["domain"] == "regent-mgt.com"
    assert body["city"] == "Nairobi"
    assert body["quality_score"] == 78
    assert body["review_status"] == "enriched"
    assert body["contact_ready"] is True

    assert len(body["contacts"]) == 1

    returned_contact = body["contacts"][0]

    assert returned_contact["name"] == "Candy Otundo"
    assert returned_contact["title"] == "Property Manager"
    assert returned_contact["email"] == "candy@example.com"
    assert returned_contact["phone"] == "+254711111111"
    assert returned_contact["enrichment_status"] == "enriched"
    assert (
        returned_contact["contact_enrichment_status"]
        == "complete"
    )
    assert (
        returned_contact["contact_enrichment_request_id"]
        == "request-123"
    )


def test_get_missing_persisted_prospect_returns_404():
    app, _ = make_app_and_db()

    client = TestClient(app)

    response = client.get(
        "/api/apollo/prospects/999999"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "Apollo prospect not found"
    )
