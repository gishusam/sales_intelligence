from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import CurrentUser, get_current_user
from app.config import settings
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
        try:
            yield db
        finally:
            pass

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


def test_contact_enrichment_starts_waterfall_and_marks_pending(
    monkeypatch,
):
    app, db = make_app_and_db()

    monkeypatch.setattr(
        settings,
        "APOLLO_API_KEY",
        "test-apollo-key",
    )
    monkeypatch.setattr(
        settings,
        "APOLLO_WEBHOOK_SECRET",
        "test-webhook-secret",
    )

    prospect = ApolloProspect(
        apollo_organization_id="5e562b21b0b5190001a53287",
        name="Centum Real Estate",
        normalized_name="centum real estate",
        review_status="enriched",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="68527052713b92000135dac5",
        first_name="Kenneth",
        last_name="Mbae",
        name="Kenneth Mbae",
        title="Managing Director",
        linkedin_url=(
            "http://www.linkedin.com/in/"
            "kenneth-mbae-0b5b17a4"
        ),
        email=None,
        phone=None,
        enrichment_status="enriched",
    )

    db.add(contact)
    db.commit()

    prospect_id = prospect.id

    class FakeApolloClient:
        calls = []

        def __init__(self, api_key):
            assert api_key == "test-apollo-key"

        def enrich_contact_details(
            self,
            *,
            person_id,
            webhook_url,
            first_name=None,
            last_name=None,
            linkedin_url=None,
        ):
            self.__class__.calls.append({
                "person_id": person_id,
                "webhook_url": webhook_url,
                "first_name": first_name,
                "last_name": last_name,
                "linkedin_url": linkedin_url,
            })

            return {
                "person": {
                    "id": person_id,
                    "name": "Kenneth Mbae",
                },
                "waterfall": {
                    "status": "accepted",
                },
                "request_id": "1039995589705121975",
            }

    monkeypatch.setattr(
        apollo_router,
        "ApolloClient",
        FakeApolloClient,
    )

    client = TestClient(app)

    response = client.post(
        (
            f"/api/apollo/prospects/"
            f"{prospect_id}/contact-enrichment"
        )
    )

    assert response.status_code == 200

    body = response.json()

    assert body["id"] == prospect_id
    assert body["contact_id"] == contact.id
    assert body["contact_name"] == "Kenneth Mbae"
    assert body["contact_enrichment_status"] == "pending"
    assert body["request_id"] == "1039995589705121975"

    assert len(FakeApolloClient.calls) == 1

    call = FakeApolloClient.calls[0]

    assert call["person_id"] == (
        "68527052713b92000135dac5"
    )
    assert call["first_name"] == "Kenneth"
    assert call["last_name"] == "Mbae"
    assert call["linkedin_url"] == (
        "http://www.linkedin.com/in/"
        "kenneth-mbae-0b5b17a4"
    )
    assert (
        "/api/apollo/webhooks/contact-enrichment"
        in call["webhook_url"]
    )
    assert (
        "token=test-webhook-secret"
        in call["webhook_url"]
    )

    db.refresh(contact)

    assert contact.contact_enrichment_status == "pending"
    assert contact.contact_enrichment_request_id == (
        "1039995589705121975"
    )


def test_pending_contact_enrichment_does_not_spend_again(
    monkeypatch,
):
    app, db = make_app_and_db()

    monkeypatch.setattr(
        settings,
        "APOLLO_API_KEY",
        "test-apollo-key",
    )
    monkeypatch.setattr(
        settings,
        "APOLLO_WEBHOOK_SECRET",
        "test-webhook-secret",
    )

    prospect = ApolloProspect(
        apollo_organization_id="org-pending-contact",
        name="Pending Contact Company",
        normalized_name="pending contact company",
        review_status="enriched",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-pending",
        name="Pending Person",
        title="Managing Director",
        enrichment_status="enriched",
        contact_enrichment_status="pending",
        contact_enrichment_request_id="existing-request",
    )

    db.add(contact)
    db.commit()

    class FakeApolloClient:
        calls = 0

        def __init__(self, api_key):
            pass

        def enrich_contact_details(self, **kwargs):
            self.__class__.calls += 1
            raise AssertionError(
                "Apollo must not be called twice"
            )

    monkeypatch.setattr(
        apollo_router,
        "ApolloClient",
        FakeApolloClient,
    )

    client = TestClient(app)

    response = client.post(
        (
            f"/api/apollo/prospects/"
            f"{prospect.id}/contact-enrichment"
        )
    )

    assert response.status_code == 200
    assert FakeApolloClient.calls == 0

    assert response.json()[
        "contact_enrichment_status"
    ] == "pending"

    assert response.json()["request_id"] == (
        "existing-request"
    )
