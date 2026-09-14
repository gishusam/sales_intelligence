import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.apollo_prospect import ApolloProspect, ApolloProspectContact
from app.models.apollo_search_run import ApolloSearchRun, ApolloSearchRunProspect
from app.models.lead import Lead
from app.services.apollo_queue import (
    ApolloEnrichmentAlreadyRunning,
    _PROCESS_ENRICHMENT_LOCK,
    enrich_search_run,
)
from app.services.apollo_enrichment import apply_contact_details_webhook


def _queued_run(count=2):
    engine = create_engine("sqlite:///:memory:")
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
    db = sessionmaker(bind=engine)()
    run = ApolloSearchRun(
        filters={"locations": ["Nairobi"], "business_types": ["developers"]},
        assigned_to="Jane Sales",
        status="queued",
        found_count=count,
        queued_count=count,
    )
    db.add(run)
    db.flush()
    for index in range(count):
        prospect = ApolloProspect(
            apollo_organization_id=f"org-{index}",
            name=f"Developer {index}",
            review_status="discovered",
        )
        db.add(prospect)
        db.flush()
        db.add(
            ApolloSearchRunProspect(
                search_run_id=run.id,
                prospect_id=prospect.id,
                status="queued",
            )
        )
    db.commit()
    return db, run


def test_unverifiable_balance_preserves_queue_and_makes_no_paid_calls():
    db, run = _queued_run()

    class FakeClient:
        paid_calls = 0

        def get_credit_usage(self):
            raise httpx.ConnectError("credit endpoint unavailable")

        def enrich_contact_details(self, **kwargs):
            self.paid_calls += 1

    client = FakeClient()
    result = enrich_search_run(
        db,
        client,
        run.id,
        webhook_url="https://example.test/webhook",
    )

    assert client.paid_calls == 0
    assert result.status == "credit_unavailable"
    assert run.status == "credit_unavailable"
    assert run.queued_count == 2
    assert db.query(ApolloSearchRunProspect).filter_by(status="queued").count() == 2


def test_insufficient_verified_balance_waits_without_spending():
    db, run = _queued_run()

    class FakeClient:
        paid_calls = 0

        def get_credit_usage(self):
            return {
                "credit_usage_stats": {
                    "lead_credit": {"limit": 100, "consumed": 96, "left_over": 4}
                },
                "current_credit_cycle": {"end_date": "2026-10-01"},
            }

        def enrich_contact_details(self, **kwargs):
            self.paid_calls += 1

    client = FakeClient()
    result = enrich_search_run(
        db, client, run.id, webhook_url="https://example.test/webhook"
    )

    assert client.paid_calls == 0
    assert result.status == "waiting_for_credits"
    assert run.status == "waiting_for_credits"
    assert run.billing_cycle_reset_at == "2026-10-01"
    assert run.queued_count == 2


def test_enrichment_selects_contact_internally_and_marks_attempt_pending():
    db, run = _queued_run(count=1)

    class FakeClient:
        enriched_person_id = None

        def get_credit_usage(self):
            return {
                "credit_usage_stats": {
                    "lead_credit": {"limit": 100, "consumed": 91, "left_over": 9}
                },
                "current_credit_cycle": {"end_date": "2026-10-01"},
            }

        def search_people(self, **kwargs):
            assert kwargs["organization_ids"] == ["org-0"]
            assert kwargs["titles"]
            assert kwargs["seniorities"]
            return {
                "people": [
                    {"id": "person-manager", "name": "Mina Manager", "title": "Manager", "seniority": "manager", "organization_id": "org-0"},
                    {"id": "person-founder", "name": "Fiona Founder", "title": "Founder", "seniority": "founder", "organization_id": "org-0"},
                ]
            }

        def enrich_contact_details(self, **kwargs):
            self.enriched_person_id = kwargs["person_id"]
            return {
                "request_id": "request-1",
                "phone_enrichment": {"status": "pending"},
                "person": {"id": kwargs["person_id"]},
            }

    client = FakeClient()
    result = enrich_search_run(
        db, client, run.id, webhook_url="https://example.test/webhook"
    )
    item = db.query(ApolloSearchRunProspect).one()

    assert client.enriched_person_id == "person-founder"
    assert result.status == "awaiting_webhooks"
    assert item.status == "pending"
    assert item.attempts == 1
    assert item.contact_id is not None
    assert run.processed_count == 1
    assert run.queued_count == 0


def test_company_without_suitable_contact_is_terminal_without_paid_call():
    db, run = _queued_run(count=1)

    class FakeClient:
        paid_calls = 0

        def get_credit_usage(self):
            return {
                "credit_usage_stats": {
                    "lead_credit": {"limit": 100, "consumed": 0, "left_over": 100}
                }
            }

        def search_people(self, **kwargs):
            return {"people": []}

        def enrich_contact_details(self, **kwargs):
            self.paid_calls += 1

    client = FakeClient()
    result = enrich_search_run(
        db, client, run.id, webhook_url="https://example.test/webhook"
    )
    item = db.query(ApolloSearchRunProspect).one()

    assert client.paid_calls == 0
    assert item.status == "no_contact"
    assert run.no_contact_count == 1
    assert run.processed_count == 1
    assert run.queued_count == 0
    assert result.status == "complete"


def test_failed_paid_request_is_recorded_without_discarding_other_queue_items():
    db, run = _queued_run(count=2)

    class FakeClient:
        def get_credit_usage(self):
            return {
                "credit_usage_stats": {
                    "lead_credit": {"limit": 100, "consumed": 82, "left_over": 18}
                }
            }

        def search_people(self, organization_ids, **kwargs):
            suffix = organization_ids[0].split("-")[-1]
            return {"people": [{"id": f"person-{suffix}", "name": "Jane", "title": "Founder", "seniority": "founder", "organization_id": organization_ids[0]}]}

        def enrich_contact_details(self, **kwargs):
            raise httpx.ReadTimeout("Apollo timed out")

    result = enrich_search_run(
        db, FakeClient(), run.id, webhook_url="https://example.test/webhook"
    )
    items = db.query(ApolloSearchRunProspect).order_by(ApolloSearchRunProspect.id).all()

    assert items[0].status == "failed"
    assert items[0].last_error == "Apollo timed out"
    assert items[1].status == "queued"
    assert run.failed_count == 1
    assert run.processed_count == 1
    assert run.queued_count == 1
    assert result.status == "failed"


def test_account_wide_lock_rejects_overlapping_enrichment():
    db, run = _queued_run(count=1)

    class FakeClient:
        credit_calls = 0

        def get_credit_usage(self):
            self.credit_calls += 1

    client = FakeClient()
    assert _PROCESS_ENRICHMENT_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(ApolloEnrichmentAlreadyRunning):
            enrich_search_run(
                db,
                client,
                run.id,
                webhook_url="https://example.test/webhook",
            )
    finally:
        _PROCESS_ENRICHMENT_LOCK.release()

    assert client.credit_calls == 0


def test_sync_email_then_phone_only_webhook_auto_imports_exactly_once():
    db, run = _queued_run(count=1)

    class FakeClient:
        def get_credit_usage(self):
            return {
                "credit_usage_stats": {
                    "lead_credit": {"limit": 100, "consumed": 91, "left_over": 9}
                }
            }

        def search_people(self, **kwargs):
            return {
                "people": [
                    {
                        "id": "person-real-flow",
                        "name": "Rita Founder",
                        "title": "Founder",
                        "seniority": "founder",
                        "organization_id": "org-0",
                    }
                ]
            }

        def enrich_contact_details(self, **kwargs):
            return {
                "request_id": "phone-request-real-flow",
                "person": {
                    "id": "person-real-flow",
                    "first_name": "Rita",
                    "last_name": "Founder",
                    "name": "Rita Founder",
                    "title": "Founder",
                    "email": "rita@example.com",
                },
                "phone_enrichment": {"status": "pending"},
            }

    enrich_search_run(
        db,
        FakeClient(),
        run.id,
        webhook_url="https://example.test/webhook",
    )
    contact = db.query(ApolloProspectContact).one()

    assert contact.email == "rita@example.com"
    assert contact.phone is None
    assert db.query(Lead).count() == 0
    db.commit()

    payload = {
        "people": [
            {
                "id": "person-real-flow",
                "phone_numbers": [{"sanitized_number": "+254700111222"}],
            }
        ]
    }
    apply_contact_details_webhook(db, payload)
    db.commit()
    apply_contact_details_webhook(db, payload)
    db.commit()

    db.refresh(contact)
    assert contact.email == "rita@example.com"
    assert contact.phone == "+254700111222"
    assert db.query(Lead).count() == 1
    assert run.imported_count == 1


def test_http_200_without_pending_phone_acceptance_marks_item_failed():
    db, run = _queued_run(count=1)

    class FakeClient:
        def get_credit_usage(self):
            return {
                "credit_usage_stats": {
                    "lead_credit": {"limit": 100, "consumed": 91, "left_over": 9}
                }
            }

        def search_people(self, **kwargs):
            return {
                "people": [
                    {
                        "id": "person-rejected-phone",
                        "name": "Rita Founder",
                        "title": "Founder",
                        "seniority": "founder",
                        "organization_id": "org-0",
                    }
                ]
            }

        def enrich_contact_details(self, **kwargs):
            return {
                "person": {
                    "id": "person-rejected-phone",
                    "email": "rita@example.com",
                },
                "phone_enrichment": {
                    "status": "not_found",
                    "message": "No phone accepted",
                },
            }

    result = enrich_search_run(
        db,
        FakeClient(),
        run.id,
        webhook_url="https://example.test/webhook",
    )
    item = db.query(ApolloSearchRunProspect).one()
    contact = db.query(ApolloProspectContact).one()

    assert item.status == "failed"
    assert item.last_error == "No phone accepted"
    assert contact.contact_enrichment_status != "pending"
    assert run.failed_count == 1
    assert result.status == "failed"


def test_queue_association_is_committed_before_phone_request(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'apollo-race.db'}")
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
    run = ApolloSearchRun(
        filters={},
        assigned_to="Jane Sales",
        status="queued",
        found_count=1,
        queued_count=1,
    )
    prospect = ApolloProspect(
        apollo_organization_id="org-race",
        name="Race Developer",
        review_status="discovered",
    )
    db.add_all([run, prospect])
    db.flush()
    db.add(
        ApolloSearchRunProspect(
            search_run_id=run.id,
            prospect_id=prospect.id,
            status="queued",
        )
    )
    db.commit()

    class FakeClient:
        def get_credit_usage(self):
            return {
                "credit_usage_stats": {
                    "lead_credit": {"limit": 100, "consumed": 91, "left_over": 9}
                }
            }

        def search_people(self, **kwargs):
            return {
                "people": [
                    {
                        "id": "person-race",
                        "name": "Rita Founder",
                        "title": "Founder",
                        "seniority": "founder",
                        "organization_id": "org-race",
                    }
                ]
            }

        def enrich_contact_details(self, **kwargs):
            observer = Session()
            try:
                saved_contact = observer.query(ApolloProspectContact).one()
                saved_item = observer.query(ApolloSearchRunProspect).one()
                assert saved_item.contact_id == saved_contact.id
                assert saved_item.status == "requesting"
                apply_contact_details_webhook(
                    observer,
                    {
                        "people": [
                            {
                                "id": "person-race",
                                "phone_numbers": [
                                    {"sanitized_number": "+254744444444"}
                                ],
                            }
                        ]
                    },
                )
                observer.commit()
            finally:
                observer.close()
            return {
                "request_id": "request-race",
                "person": {
                    "id": "person-race",
                    "email": "rita@example.com",
                },
                "phone_enrichment": {"status": "pending"},
            }

    enrich_search_run(
        db,
        FakeClient(),
        run.id,
        webhook_url="https://example.test/webhook",
    )

    observer = Session()
    try:
        saved_contact = observer.query(ApolloProspectContact).one()
        saved_item = observer.query(ApolloSearchRunProspect).one()
        assert saved_contact.email == "rita@example.com"
        assert saved_contact.phone == "+254744444444"
        assert saved_item.status == "imported"
        assert observer.query(Lead).count() == 1
    finally:
        observer.close()


def test_existing_contact_ready_prospect_imports_without_paid_enrichment():
    db, run = _queued_run(count=1)
    prospect = db.query(ApolloProspect).one()
    db.add(
        ApolloProspectContact(
            prospect_id=prospect.id,
            apollo_person_id="person-ready",
            name="Ready Founder",
            title="Founder",
            email="ready@example.com",
            phone="+254733333333",
            enrichment_status="enriched",
            contact_enrichment_status="complete",
        )
    )
    db.commit()

    class FakeClient:
        people_calls = 0
        paid_calls = 0

        def get_credit_usage(self):
            return {
                "credit_usage_stats": {
                    "lead_credit": {"limit": 100, "consumed": 91, "left_over": 9}
                }
            }

        def search_people(self, **kwargs):
            self.people_calls += 1
            return {"people": []}

        def enrich_contact_details(self, **kwargs):
            self.paid_calls += 1

    client = FakeClient()
    result = enrich_search_run(
        db,
        client,
        run.id,
        webhook_url="https://example.test/webhook",
    )
    item = db.query(ApolloSearchRunProspect).one()

    assert client.people_calls == 0
    assert client.paid_calls == 0
    assert db.query(Lead).count() == 1
    assert item.status == "imported"
    assert run.imported_count == 1
    assert run.processed_count == 1
    assert run.queued_count == 0
    assert result.status == "complete"


def test_targeted_people_search_falls_back_to_broad_company_search():
    db, run = _queued_run(count=1)

    class FakeClient:
        search_calls = []
        enriched_person_id = None

        def get_credit_usage(self):
            return {
                "credit_usage_stats": {
                    "lead_credit": {
                        "limit": 100,
                        "consumed": 91,
                        "left_over": 9,
                    }
                }
            }

        def search_people(self, **kwargs):
            self.search_calls.append(kwargs)

            if kwargs["titles"] or kwargs["seniorities"]:
                return {"people": []}

            return {
                "people": [
                    {
                        "id": "person-sales-manager",
                        "name": "Shamila Manager",
                        "title": "Sales Manager",
                        "seniority": None,
                        "organization_id": "org-0",
                    }
                ]
            }

        def enrich_contact_details(self, **kwargs):
            self.enriched_person_id = kwargs["person_id"]
            return {
                "request_id": "request-fallback",
                "person": {
                    "id": kwargs["person_id"],
                    "name": "Shamila Manager",
                    "title": "Sales Manager",
                    "email": "shamila@example.com",
                },
                "phone_enrichment": {"status": "pending"},
            }

    client = FakeClient()

    result = enrich_search_run(
        db,
        client,
        run.id,
        webhook_url="https://example.test/webhook",
    )

    item = db.query(ApolloSearchRunProspect).one()
    contact = db.query(ApolloProspectContact).one()

    assert len(client.search_calls) == 2
    assert client.search_calls[0]["titles"]
    assert client.search_calls[1]["titles"] == []
    assert client.search_calls[1]["seniorities"] == []
    assert client.enriched_person_id == "person-sales-manager"
    assert contact.title == "Sales Manager"
    assert item.status == "pending"
    assert result.status == "awaiting_webhooks"


def test_ready_contact_imports_even_when_paid_credit_balance_is_zero():
    db, run = _queued_run(count=1)
    prospect = db.query(ApolloProspect).one()

    db.add(
        ApolloProspectContact(
            prospect_id=prospect.id,
            apollo_person_id="person-ready-zero-credit",
            name="Ready Contact",
            title="Founder",
            email="ready-zero@example.com",
            phone="+254700000001",
            enrichment_status="enriched",
            contact_enrichment_status="complete",
        )
    )
    db.commit()

    class FakeClient:
        def get_credit_usage(self):
            return {
                "credit_usage_stats": {
                    "lead_credit": {
                        "limit": 500,
                        "consumed": 125,
                        "left_over": 375,
                    },
                    "direct_dial_credit": {
                        "limit": 100,
                        "consumed": 100,
                        "left_over": 0,
                    },
                }
            }

        def search_people(self, **kwargs):
            raise AssertionError("People Search must not be called")

        def enrich_contact_details(self, **kwargs):
            raise AssertionError("Paid enrichment must not be called")

    result = enrich_search_run(
        db,
        FakeClient(),
        run.id,
        webhook_url="https://example.test/webhook",
    )

    item = db.query(ApolloSearchRunProspect).one()

    assert db.query(Lead).count() == 1
    assert item.status == "imported"
    assert run.imported_count == 1
    assert run.processed_count == 1
    assert run.queued_count == 0
    assert result.status == "complete"
