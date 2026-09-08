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


def test_import_endpoint_assigns_lead_to_logged_in_sales_rep():
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
        apollo_organization_id="org-import-route",
        name="Route Property Management",
        normalized_name="route property management",
        domain="route.co.ke",
        website_url="https://route.co.ke",
        city="Nairobi",
        quality_score=86,
        review_status="approved",
    )

    db.add(prospect)
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

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(app)

    response = client.post(
        f"/api/apollo/prospects/{prospect_id}/import"
    )

    assert response.status_code == 200

    lead = db.query(Lead).one()

    assert response.json() == {
        "id": prospect_id,
        "lead_id": lead.id,
        "assigned_to": "Jane Sales",
    }

    assert lead.assigned_to == "Jane Sales"
    assert lead.source == "apollo"
    assert lead.status == "new"

    db.refresh(prospect)
    assert prospect.imported_lead_id == lead.id


def test_import_endpoint_returns_conflict_for_non_approved_prospect():
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
        apollo_organization_id="org-import-route-conflict",
        name="Pending Route Prospect",
        normalized_name="pending route prospect",
        review_status="pending_review",
    )

    db.add(prospect)
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

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(app)

    response = client.post(
        f"/api/apollo/prospects/{prospect_id}/import"
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "cannot import pending_review prospect"
    }

    assert db.query(Lead).count() == 0


def test_import_endpoint_returns_not_found_for_missing_prospect():
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
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(
        app,
        raise_server_exceptions=False,
    )

    response = client.post(
        "/api/apollo/prospects/999/import"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Apollo prospect not found"
    }
