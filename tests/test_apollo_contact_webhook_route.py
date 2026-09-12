from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.models.apollo_prospect import (
    ApolloProspect,
    ApolloProspectContact,
)
from app.models.lead import Lead
from app.models.apollo_search_run import ApolloSearchRun, ApolloSearchRunProspect
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
            ApolloSearchRun.__table__,
            ApolloSearchRunProspect.__table__,
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

    return app, db


def test_contact_webhook_updates_contact_and_imported_lead(
    monkeypatch,
):
    app, db = make_app_and_db()

    monkeypatch.setattr(
        settings,
        "APOLLO_WEBHOOK_SECRET",
        "test-webhook-secret",
        raising=False,
    )

    lead = Lead(
        name="Centum Real Estate",
        lead_type="agency",
        source="apollo",
        status="new",
        assigned_to="Samuel Ngugi",
        contact_person="Kenneth Mbae",
        contact_person_role="Managing Director",
        email=None,
        phone=None,
    )

    db.add(lead)
    db.flush()

    prospect = ApolloProspect(
        apollo_organization_id="5e562b21b0b5190001a53287",
        name="Centum Real Estate",
        normalized_name="centum real estate",
        review_status="imported",
        imported_lead_id=lead.id,
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
        email=None,
        phone=None,
        enrichment_status="enriched",
    )

    db.add(contact)
    db.commit()

    client = TestClient(app)

    response = client.post(
        (
            "/api/apollo/webhooks/contact-enrichment"
            "?token=test-webhook-secret"
        ),
        json={
            "status": "success",
            "people": [
                {
                    "id": "68527052713b92000135dac5",
                    "emails": [
                        {
                            "email": "kenneth@centum.co.ke",
                            "email_status_cd": "Verified",
                        }
                    ],
                    "phone_numbers": [
                        {
                            "sanitized_number": "+254712345678",
                            "status_cd": "valid_number",
                            "type_cd": "mobile",
                        }
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "updated": 1,
    }

    db.refresh(contact)
    db.refresh(lead)

    assert contact.email == "kenneth@centum.co.ke"
    assert contact.phone == "+254712345678"

    # An Apollo prospect may already have been imported
    # while contact waterfall enrichment was pending.
    assert lead.email == "kenneth@centum.co.ke"
    assert lead.phone == "+254712345678"


def test_contact_webhook_rejects_wrong_secret(
    monkeypatch,
):
    app, _db = make_app_and_db()

    monkeypatch.setattr(
        settings,
        "APOLLO_WEBHOOK_SECRET",
        "correct-secret",
        raising=False,
    )

    client = TestClient(app)

    response = client.post(
        (
            "/api/apollo/webhooks/contact-enrichment"
            "?token=wrong-secret"
        ),
        json={
            "status": "success",
            "people": [],
        },
    )

    assert response.status_code == 403


def test_contact_ready_webhook_auto_imports_once_and_completes_run(monkeypatch):
    app, db = make_app_and_db()
    monkeypatch.setattr(
        settings,
        "APOLLO_WEBHOOK_SECRET",
        "test-webhook-secret",
        raising=False,
    )
    run = ApolloSearchRun(
        filters={"locations": ["Nairobi"]},
        assigned_to="Jane Sales",
        status="awaiting_webhooks",
        found_count=1,
        processed_count=1,
        queued_count=0,
    )
    prospect = ApolloProspect(
        name="Webhook Developer",
        city="Nairobi",
        review_status="discovered",
    )
    db.add_all([run, prospect])
    db.flush()
    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-webhook-auto",
        name="Wendy Founder",
        title="Founder",
        enrichment_status="not_enriched",
        contact_enrichment_status="pending",
    )
    db.add(contact)
    db.flush()
    item = ApolloSearchRunProspect(
        search_run_id=run.id,
        prospect_id=prospect.id,
        contact_id=contact.id,
        status="pending",
        attempts=1,
    )
    db.add(item)
    db.commit()

    payload = {
        "people": [
            {
                "id": "person-webhook-auto",
                "emails": [{"email": "wendy@example.com"}],
                "phone_numbers": [{"sanitized_number": "+254711111111"}],
            }
        ]
    }
    client = TestClient(app)
    first = client.post(
        "/api/apollo/webhooks/contact-enrichment?token=test-webhook-secret",
        json=payload,
    )
    second = client.post(
        "/api/apollo/webhooks/contact-enrichment?token=test-webhook-secret",
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert db.query(Lead).count() == 1
    lead = db.query(Lead).one()
    assert lead.assigned_to == "Jane Sales"
    assert lead.source == "apollo"
    assert lead.status == "new"
    assert item.status == "imported"
    assert run.imported_count == 1
    assert run.status == "complete"


def test_empty_contact_webhook_marks_pending_item_no_contact(monkeypatch):
    app, db = make_app_and_db()
    monkeypatch.setattr(settings, "APOLLO_WEBHOOK_SECRET", "secret", raising=False)
    run = ApolloSearchRun(
        filters={},
        assigned_to="Jane Sales",
        status="awaiting_webhooks",
        found_count=1,
        processed_count=1,
        queued_count=0,
    )
    prospect = ApolloProspect(name="No Contact Ltd", review_status="discovered")
    db.add_all([run, prospect])
    db.flush()
    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-empty",
        enrichment_status="not_enriched",
        contact_enrichment_status="pending",
    )
    db.add(contact)
    db.flush()
    item = ApolloSearchRunProspect(
        search_run_id=run.id,
        prospect_id=prospect.id,
        contact_id=contact.id,
        status="pending",
    )
    db.add(item)
    db.commit()

    response = TestClient(app).post(
        "/api/apollo/webhooks/contact-enrichment?token=secret",
        json={"people": [{"id": "person-empty", "emails": [], "phone_numbers": []}]},
    )

    assert response.status_code == 200
    assert item.status == "no_contact"
    assert run.no_contact_count == 1
    assert run.status == "complete"
