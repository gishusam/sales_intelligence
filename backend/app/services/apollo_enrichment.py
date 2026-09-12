import httpx
from datetime import datetime, timezone

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models.apollo_prospect import (
    ApolloProspect,
    ApolloProspectContact,
)
from app.models.lead import Lead
from app.models.apollo_search_run import ApolloSearchRun, ApolloSearchRunProspect
from app.services.apollo_persistence import (
    apply_contact_enrichment,
    apply_prospect_enrichment,
    auto_import_contact_ready_prospect,
    mark_contact_enrichment_failed,
)
from app.services.apollo_scoring import score_prospect


def enrich_prospect_contact(
    db: Session,
    client,
    contact_id: int,
) -> ApolloProspectContact:
    contact = (
        db.query(ApolloProspectContact)
        .filter(ApolloProspectContact.id == contact_id)
        .one()
    )

    try:
        result = client.enrich_person(
            person_id=contact.apollo_person_id,
        )
    except httpx.HTTPError:
        return mark_contact_enrichment_failed(
            db,
            contact.id,
        )

    person = result.get("person", {})

    return apply_contact_enrichment(
        db,
        contact.id,
        first_name=person.get("first_name"),
        last_name=person.get("last_name"),
        name=person.get("name"),
        title=person.get("title"),
        linkedin_url=person.get("linkedin_url"),
        email=person.get("email"),
    )



def _decision_maker_priority(
    contact: ApolloProspectContact,
) -> int:
    title = (contact.title or "").strip().lower()
    seniority = (contact.seniority or "").strip().lower()

    title_scores = {
        "founder": 25,
        "owner": 25,
        "ceo": 25,
        "chief executive officer": 25,
        "managing director": 25,
        "coo": 23,
        "chief operating officer": 23,
        "head of operations": 23,
        "head of property management": 23,
        "operations manager": 20,
        "property manager": 20,
        "finance director": 18,
        "head of finance": 18,
        "finance manager": 15,
    }

    title_score = title_scores.get(title)

    if title_score is None:
        if "director" in title:
            title_score = 12
        elif "manager" in title:
            title_score = 8
        else:
            title_score = 0

    seniority_scores = {
        "owner": 10,
        "founder": 10,
        "c_suite": 10,
        "head": 8,
        "director": 6,
        "manager": 4,
    }

    return min(
        title_score + seniority_scores.get(seniority, 0),
        35,
    )


def _select_best_contact(
    contacts: list[ApolloProspectContact],
) -> ApolloProspectContact:
    enrichable = [
        contact
        for contact in contacts
        if contact.apollo_person_id
    ]

    if not enrichable:
        raise ValueError(
            "prospect has no enrichable decision maker"
        )

    return max(
        enrichable,
        key=lambda contact: (
            _decision_maker_priority(contact),
            -(contact.id or 0),
        ),
    )


def enrich_prospect(
    db: Session,
    client,
    prospect_id: int,
) -> ApolloProspect:
    prospect = (
        db.query(ApolloProspect)
        .filter(ApolloProspect.id == prospect_id)
        .one()
    )

    contacts = (
        db.query(ApolloProspectContact)
        .filter(
            ApolloProspectContact.prospect_id
            == prospect.id
        )
        .order_by(ApolloProspectContact.id)
        .all()
    )

    selected_contact = _select_best_contact(contacts)

    organization_result = client.enrich_organization(
        domain=prospect.domain,
        linkedin_url=prospect.linkedin_url,
        website=prospect.website_url,
        name=prospect.name,
    )

    organization = (
        organization_result.get("organization") or {}
    )

    apply_prospect_enrichment(
        db,
        prospect.id,
        domain=organization.get("primary_domain"),
        website_url=organization.get("website_url"),
        linkedin_url=organization.get("linkedin_url"),
        employee_count=organization.get(
            "estimated_num_employees"
        ),
        city=organization.get("city"),
        country=organization.get("country"),
        industry=organization.get("industry"),
    )

    enriched_contact = enrich_prospect_contact(
        db,
        client,
        selected_contact.id,
    )

    if enriched_contact.enrichment_status != "enriched":
        raise ValueError(
            "Apollo decision-maker enrichment failed"
        )

    scoring_data = {
        "apollo_organization_id": (
            prospect.apollo_organization_id
        ),
        "name": prospect.name,
        "domain": prospect.domain,
        "website_url": prospect.website_url,
        "linkedin_url": prospect.linkedin_url,
        "employee_count": prospect.employee_count,
        "city": prospect.city,
        "country": prospect.country,
        "industry": prospect.industry,
        "keywords": prospect.keywords or [],
        "decision_makers": [
            {
                "apollo_person_id": contact.apollo_person_id,
                "title": contact.title,
                "seniority": contact.seniority,
                "linkedin_url": contact.linkedin_url,
            }
            for contact in contacts
        ],
    }

    score = score_prospect(scoring_data)

    prospect.quality_score = score["quality_score"]
    prospect.quality_band = score["quality_band"]
    prospect.score_breakdown = score["score_breakdown"]
    prospect.score_reasons = score["score_reasons"]
    prospect.review_status = "enriched"

    db.flush()

    return prospect


def _webhook_email(person: dict) -> str | None:
    emails = person.get("emails") or []

    for item in emails:
        email = item.get("email")

        if email:
            return email.strip()

    return None


def _webhook_phone(person: dict) -> str | None:
    phone_numbers = person.get("phone_numbers") or []

    for item in phone_numbers:
        phone = (
            item.get("sanitized_number")
            or item.get("raw_number")
        )

        if phone:
            return phone.strip()

    return None


def apply_contact_details_webhook(
    db: Session,
    payload: dict,
) -> int:
    updated_count = 0
    has_queue_table = inspect(db.get_bind()).has_table(
        "apollo_search_run_prospects"
    )

    for person in payload.get("people") or []:
        person_id = person.get("id")

        if not person_id:
            continue

        contact = (
            db.query(ApolloProspectContact)
            .filter(
                ApolloProspectContact.apollo_person_id
                == person_id
            )
            .one_or_none()
        )

        if contact is None:
            continue

        email = _webhook_email(person)
        phone = _webhook_phone(person)

        if email is None and phone is None:
            contact.contact_enrichment_status = "not_found"
            if has_queue_table:
                queue_item = (
                    db.query(ApolloSearchRunProspect)
                    .filter(ApolloSearchRunProspect.contact_id == contact.id)
                    .one_or_none()
                )
                if queue_item is not None:
                    run = (
                        db.query(ApolloSearchRun)
                        .filter(ApolloSearchRun.id == queue_item.search_run_id)
                        .one()
                    )
                    if queue_item.status == "pending":
                        queue_item.status = "no_contact"
                        run.no_contact_count = (run.no_contact_count or 0) + 1
                    db.flush()
                    remaining = (
                        db.query(ApolloSearchRunProspect)
                        .filter(
                            ApolloSearchRunProspect.search_run_id == run.id,
                            ApolloSearchRunProspect.status.in_(
                                ["queued", "pending"]
                            ),
                        )
                        .count()
                    )
                    if remaining == 0:
                        run.status = "complete"
                        run.enrichment_completed_at = datetime.now(timezone.utc)
            updated_count += 1
            continue

        apply_contact_enrichment(
            db,
            contact.id,
            email=email,
            phone=phone,
        )

        if contact.email and contact.phone:
            contact.contact_enrichment_status = "complete"
        elif contact.email or contact.phone:
            contact.contact_enrichment_status = "partial"
        else:
            contact.contact_enrichment_status = "not_found"

        prospect = (
            db.query(ApolloProspect)
            .filter(
                ApolloProspect.id
                == contact.prospect_id
            )
            .one_or_none()
        )

        if (
            prospect is not None
            and prospect.imported_lead_id is not None
        ):
            lead = (
                db.query(Lead)
                .filter(
                    Lead.id
                    == prospect.imported_lead_id
                )
                .one_or_none()
            )

            if lead is not None:
                if email is not None:
                    lead.email = email

                if phone is not None:
                    lead.phone = phone

        if (
            prospect is not None
            and contact.email
            and contact.phone
            and has_queue_table
        ):
            queue_item = (
                db.query(ApolloSearchRunProspect)
                .filter(
                    ApolloSearchRunProspect.contact_id == contact.id,
                )
                .one_or_none()
            )
            if queue_item is not None:
                run = (
                    db.query(ApolloSearchRun)
                    .filter(
                        ApolloSearchRun.id == queue_item.search_run_id
                    )
                    .one()
                )
                auto_import_contact_ready_prospect(
                    db,
                    prospect.id,
                    assigned_to=run.assigned_to or "Apollo",
                )
                if queue_item.status != "imported":
                    queue_item.status = "imported"
                    run.imported_count = (run.imported_count or 0) + 1

                remaining = (
                    db.query(ApolloSearchRunProspect)
                    .filter(
                        ApolloSearchRunProspect.search_run_id == run.id,
                        ApolloSearchRunProspect.status.in_(
                            ["queued", "pending"]
                        ),
                    )
                    .count()
                )
                if remaining == 0:
                    run.status = "complete"
                    run.enrichment_completed_at = datetime.now(timezone.utc)

        updated_count += 1

    db.flush()

    return updated_count


def request_contact_enrichment(
    db: Session,
    client,
    prospect_id: int,
    *,
    webhook_url: str,
) -> ApolloProspectContact:
    prospect = (
        db.query(ApolloProspect)
        .filter(ApolloProspect.id == prospect_id)
        .one()
    )

    if prospect.review_status not in {
        "enriched",
        "pending_review",
        "approved",
        "imported",
    }:
        raise ValueError(
            f"cannot enrich contact for "
            f"{prospect.review_status} prospect"
        )

    contacts = (
        db.query(ApolloProspectContact)
        .filter(
            ApolloProspectContact.prospect_id
            == prospect.id,
            ApolloProspectContact.enrichment_status
            == "enriched",
        )
        .order_by(ApolloProspectContact.id)
        .all()
    )

    contact = _select_best_contact(contacts)

    if contact.email and contact.phone:
        contact.contact_enrichment_status = "complete"
        db.flush()
        return contact

    if contact.contact_enrichment_status == "pending":
        return contact

    result = client.enrich_contact_details(
        person_id=contact.apollo_person_id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        linkedin_url=contact.linkedin_url,
        webhook_url=webhook_url,
    )

    phone_enrichment = (
        result.get("phone_enrichment") or {}
    )

    if phone_enrichment.get("status") != "pending":
        raise ValueError(
            phone_enrichment.get("message")
            or "Apollo phone enrichment was not accepted"
        )

    request_id = result.get("request_id")

    contact.contact_enrichment_status = "pending"
    contact.contact_enrichment_request_id = (
        str(request_id)
        if request_id is not None
        else None
    )

    db.flush()

    return contact
