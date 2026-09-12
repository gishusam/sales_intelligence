from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
from threading import Lock

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.apollo_prospect import ApolloProspect, ApolloProspectContact
from app.models.apollo_search_run import ApolloSearchRun, ApolloSearchRunProspect
from app.services.apollo_credits import (
    CreditBalanceUnavailable,
    normalize_credit_budget,
)
from app.services.apollo_enrichment import _select_best_contact
from app.services.apollo_normalizer import normalize_person
from app.services.apollo_persistence import upsert_prospect_contact


DEFAULT_CONTACT_TITLES = [
    "Founder",
    "Owner",
    "CEO",
    "Managing Director",
    "COO",
    "Head of Operations",
    "Operations Manager",
    "Property Manager",
    "Finance Director",
    "Finance Manager",
]
DEFAULT_CONTACT_SENIORITIES = [
    "owner",
    "founder",
    "c_suite",
    "head",
    "director",
    "manager",
]

_PROCESS_ENRICHMENT_LOCK = Lock()
_POSTGRES_ADVISORY_LOCK_ID = 730120260912


class ApolloEnrichmentAlreadyRunning(RuntimeError):
    """Raised when another account-wide Apollo batch owns the lock."""


@contextmanager
def _account_enrichment_lock(db: Session):
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        acquired = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
            {"lock_id": _POSTGRES_ADVISORY_LOCK_ID},
        ).scalar()
        if not acquired:
            raise ApolloEnrichmentAlreadyRunning(
                "Another Apollo enrichment batch is already running"
            )
        yield
        return

    if not _PROCESS_ENRICHMENT_LOCK.acquire(blocking=False):
        raise ApolloEnrichmentAlreadyRunning(
            "Another Apollo enrichment batch is already running"
        )
    try:
        yield
    finally:
        _PROCESS_ENRICHMENT_LOCK.release()


@dataclass(frozen=True)
class EnrichmentRunResult:
    run: ApolloSearchRun
    status: str


def _enrich_search_run_unlocked(
    db: Session,
    client,
    run_id: int,
    *,
    webhook_url: str,
) -> EnrichmentRunResult:
    run = (
        db.query(ApolloSearchRun)
        .filter(ApolloSearchRun.id == run_id)
        .one()
    )

    try:
        budget = normalize_credit_budget(client.get_credit_usage())
    except (httpx.HTTPError, CreditBalanceUnavailable, TypeError, ValueError):
        run.status = "credit_unavailable"
        run.credit_status = {"verified": False}
        db.flush()
        return EnrichmentRunResult(run=run, status="credit_unavailable")

    run.credit_status = budget.as_dict()
    run.billing_cycle_reset_at = budget.reset_date
    if not budget.can_enrich_contact():
        run.status = "waiting_for_credits"
        db.flush()
        return EnrichmentRunResult(
            run=run,
            status="waiting_for_credits",
        )

    reserved_attempts = 0
    queued_items = (
        db.query(ApolloSearchRunProspect)
        .filter(
            ApolloSearchRunProspect.search_run_id == run.id,
            ApolloSearchRunProspect.status == "queued",
        )
        .order_by(ApolloSearchRunProspect.id)
        .all()
    )
    run.status = "enriching"
    run.enrichment_started_at = run.enrichment_started_at or datetime.now(
        timezone.utc
    )

    for item in queued_items:
        if not budget.can_enrich_contact(reserved_attempts):
            run.status = "waiting_for_credits"
            break

        prospect = (
            db.query(ApolloProspect)
            .filter(ApolloProspect.id == item.prospect_id)
            .one()
        )
        people = client.search_people(
            organization_ids=[prospect.apollo_organization_id],
            titles=DEFAULT_CONTACT_TITLES,
            seniorities=DEFAULT_CONTACT_SENIORITIES,
            page=1,
            per_page=25,
        ).get("people", [])
        contacts = [
            upsert_prospect_contact(db, prospect, normalize_person(person))
            for person in people
        ]
        if not contacts:
            item.status = "no_contact"
            item.processed_at = datetime.now(timezone.utc)
            run.no_contact_count = (run.no_contact_count or 0) + 1
            run.processed_count = (run.processed_count or 0) + 1
            run.queued_count = max((run.queued_count or 0) - 1, 0)
            continue

        contact = _select_best_contact(contacts)
        try:
            response = client.enrich_contact_details(
                person_id=contact.apollo_person_id,
                first_name=contact.first_name,
                last_name=contact.last_name,
                linkedin_url=contact.linkedin_url,
                webhook_url=webhook_url,
            )
        except httpx.HTTPError as exc:
            item.status = "failed"
            item.contact_id = contact.id
            item.attempts = (item.attempts or 0) + 1
            item.processed_at = datetime.now(timezone.utc)
            item.last_error = str(exc)
            run.failed_count = (run.failed_count or 0) + 1
            run.processed_count = (run.processed_count or 0) + 1
            run.queued_count = max((run.queued_count or 0) - 1, 0)
            run.status = "failed"
            break
        contact.contact_enrichment_status = "pending"
        request_id = response.get("request_id")
        contact.contact_enrichment_request_id = (
            str(request_id) if request_id is not None else None
        )
        item.status = "pending"
        item.contact_id = contact.id
        item.attempts = (item.attempts or 0) + 1
        item.processed_at = datetime.now(timezone.utc)
        run.processed_count = (run.processed_count or 0) + 1
        run.queued_count = max((run.queued_count or 0) - 1, 0)
        reserved_attempts += 1

    if run.queued_count == 0 and run.status == "enriching":
        pending_count = (
            db.query(ApolloSearchRunProspect)
            .filter(
                ApolloSearchRunProspect.search_run_id == run.id,
                ApolloSearchRunProspect.status == "pending",
            )
            .count()
        )
        run.status = "awaiting_webhooks" if pending_count else "complete"
        if not pending_count:
            run.enrichment_completed_at = datetime.now(timezone.utc)
    elif run.status == "enriching":
        run.status = "waiting_for_credits"

    db.flush()
    return EnrichmentRunResult(run=run, status=run.status)


def enrich_search_run(
    db: Session,
    client,
    run_id: int,
    *,
    webhook_url: str,
) -> EnrichmentRunResult:
    with _account_enrichment_lock(db):
        return _enrich_search_run_unlocked(
            db,
            client,
            run_id,
            webhook_url=webhook_url,
        )
