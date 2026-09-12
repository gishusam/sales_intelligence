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
