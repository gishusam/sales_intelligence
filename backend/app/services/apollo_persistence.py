from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.apollo_prospect import ApolloProspect, ApolloProspectContact
from app.models.apollo_search_run import (
    ApolloSearchRun,
    ApolloSearchRunProspect,
)


PROSPECT_UPDATE_FIELDS = (
    "name",
    "domain",
    "website_url",
    "linkedin_url",
    "employee_count",
    "city",
    "country",
    "industry",
    "keywords",
    "quality_score",
    "quality_band",
    "score_breakdown",
    "score_reasons",
)


def create_search_run(
    db: Session,
    *,
    filters: dict,
    assigned_to: str,
) -> ApolloSearchRun:
    run = ApolloSearchRun(
        filters=filters,
        assigned_to=assigned_to,
        status="queued",
        found_count=0,
        queued_count=0,
    )
    db.add(run)
    db.flush()
    return run


def attach_prospect_to_search_run(
    db: Session,
    run: ApolloSearchRun,
    prospect: ApolloProspect,
) -> ApolloSearchRunProspect:
    existing = (
        db.query(ApolloSearchRunProspect)
        .filter(
            ApolloSearchRunProspect.search_run_id == run.id,
            ApolloSearchRunProspect.prospect_id == prospect.id,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing

    item = ApolloSearchRunProspect(
        search_run_id=run.id,
        prospect_id=prospect.id,
        status="queued",
    )
    db.add(item)
    run.found_count = (run.found_count or 0) + 1
    run.queued_count = (run.queued_count or 0) + 1
    db.flush()
    return item


def normalize_company_name(name: str | None) -> str | None:
    if not name:
        return None
    return " ".join(name.lower().split())


def upsert_prospect(
    db: Session,
    data: dict,
    *,
    preserve_existing_enriched: bool = False,
) -> ApolloProspect:
    apollo_organization_id = data.get("apollo_organization_id")

    prospect = None

    if apollo_organization_id:
        prospect = (
            db.query(ApolloProspect)
            .filter(
                ApolloProspect.apollo_organization_id
                == apollo_organization_id
            )
            .first()
        )

    domain = data.get("domain")

    if prospect is None and domain:
        prospect = (
            db.query(ApolloProspect)
            .filter(
                func.lower(ApolloProspect.domain)
                == domain.lower()
            )
            .first()
        )

    normalized_name = normalize_company_name(
        data.get("name")
    )

    if prospect is None and normalized_name:
        prospect = (
            db.query(ApolloProspect)
            .filter(
                ApolloProspect.normalized_name
                == normalized_name
            )
            .first()
        )

    existing_prospect = prospect is not None

    if prospect is None:
        prospect = ApolloProspect(
            apollo_organization_id=apollo_organization_id,
            name=data["name"],
            normalized_name=normalized_name,
        )
        db.add(prospect)

    if (
        apollo_organization_id
        and not prospect.apollo_organization_id
    ):
        prospect.apollo_organization_id = apollo_organization_id

    preserve_enriched = (
        preserve_existing_enriched
        and existing_prospect
        and prospect.review_status != "discovered"
    )

    enrichment_score_fields = {
        "quality_score",
        "quality_band",
        "score_breakdown",
        "score_reasons",
    }

    for field in PROSPECT_UPDATE_FIELDS:
        if field not in data:
            continue

        value = data[field]

        if value is None:
            continue

        if (
            preserve_enriched
            and field in enrichment_score_fields
        ):
            continue

        if (
            preserve_enriched
            and getattr(prospect, field) is not None
        ):
            continue

        setattr(prospect, field, value)

    if data.get("name") and (
        not preserve_enriched
        or not prospect.normalized_name
    ):
        prospect.normalized_name = normalize_company_name(
            data["name"]
        )

    prospect.last_seen_at = datetime.now(timezone.utc)

    db.flush()

    return prospect


def apply_prospect_enrichment(
    db: Session,
    prospect_id: int,
    *,
    domain: str | None = None,
    website_url: str | None = None,
    linkedin_url: str | None = None,
    employee_count: int | None = None,
    city: str | None = None,
    country: str | None = None,
    industry: str | None = None,
) -> ApolloProspect:
    prospect = (
        db.query(ApolloProspect)
        .filter(ApolloProspect.id == prospect_id)
        .one()
    )

    enriched_fields = {
        "domain": domain,
        "website_url": website_url,
        "linkedin_url": linkedin_url,
        "employee_count": employee_count,
        "city": city,
        "country": country,
        "industry": industry,
    }

    for field, value in enriched_fields.items():
        if value is not None:
            setattr(prospect, field, value)

    db.flush()

    return prospect


CONTACT_UPDATE_FIELDS = (
    "first_name",
    "last_name",
    "name",
    "title",
    "seniority",
    "linkedin_url",
)


def upsert_prospect_contact(
    db: Session,
    prospect: ApolloProspect,
    data: dict,
    *,
    preserve_existing_enriched: bool = False,
) -> ApolloProspectContact:
    apollo_person_id = data.get("apollo_person_id")

    contact = None

    if apollo_person_id:
        contact = (
            db.query(ApolloProspectContact)
            .filter(
                ApolloProspectContact.prospect_id == prospect.id,
                ApolloProspectContact.apollo_person_id
                == apollo_person_id,
            )
            .first()
        )

    existing_contact = contact is not None

    if contact is None:
        contact = ApolloProspectContact(
            prospect_id=prospect.id,
            apollo_person_id=apollo_person_id,
        )
        db.add(contact)

    preserve_enriched_contact = (
        preserve_existing_enriched
        and existing_contact
        and contact.enrichment_status == "enriched"
    )

    for field in CONTACT_UPDATE_FIELDS:
        if field not in data:
            continue

        value = data[field]

        if value is None:
            continue

        if (
            preserve_enriched_contact
            and getattr(contact, field) is not None
        ):
            continue

        setattr(contact, field, value)

    db.flush()

    return contact



def persist_discovered_prospect(
    db: Session,
    data: dict,
) -> ApolloProspect:
    prospect = upsert_prospect(
        db,
        data,
        preserve_existing_enriched=True,
    )

    for contact_data in data.get("decision_makers", []):
        upsert_prospect_contact(
            db,
            prospect,
            contact_data,
            preserve_existing_enriched=True,
        )

    return prospect


def move_prospect_to_review_queue(
    db: Session,
    prospect_id: int,
) -> ApolloProspect:
    prospect = (
        db.query(ApolloProspect)
        .filter(ApolloProspect.id == prospect_id)
        .one()
    )

    if prospect.review_status != "enriched":
        raise ValueError(
            f"cannot move {prospect.review_status} "
            "prospect to review queue"
        )

    sales_ready_contact = (
        db.query(ApolloProspectContact)
        .filter(
            ApolloProspectContact.prospect_id
            == prospect.id,
            ApolloProspectContact.enrichment_status
            == "enriched",
            ApolloProspectContact.email.isnot(None),
            ApolloProspectContact.phone.isnot(None),
        )
        .first()
    )

    if sales_ready_contact is None:
        raise ValueError(
            "prospect must have both email and phone "
            "before review"
        )

    prospect.review_status = "pending_review"

    db.flush()

    return prospect


def approve_prospect(
    db: Session,
    prospect_id: int,
) -> ApolloProspect:
    prospect = (
        db.query(ApolloProspect)
        .filter(ApolloProspect.id == prospect_id)
        .one()
    )

    if prospect.review_status != "pending_review":
        raise ValueError(
            f"cannot approve {prospect.review_status} prospect"
        )

    prospect.review_status = "approved"

    db.flush()

    return prospect


def reject_prospect(
    db: Session,
    prospect_id: int,
) -> ApolloProspect:
    prospect = (
        db.query(ApolloProspect)
        .filter(ApolloProspect.id == prospect_id)
        .one()
    )

    if prospect.review_status != "pending_review":
        raise ValueError(
            f"cannot reject {prospect.review_status} prospect"
        )

    prospect.review_status = "rejected"

    db.flush()

    return prospect


def apply_contact_enrichment(
    db: Session,
    contact_id: int,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    name: str | None = None,
    title: str | None = None,
    linkedin_url: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> ApolloProspectContact:
    contact = (
        db.query(ApolloProspectContact)
        .filter(ApolloProspectContact.id == contact_id)
        .one()
    )

    enriched_fields = {
        "first_name": first_name,
        "last_name": last_name,
        "name": name,
        "title": title,
        "linkedin_url": linkedin_url,
        "email": email,
        "phone": phone,
    }

    for field, value in enriched_fields.items():
        if value is not None:
            setattr(contact, field, value)

    contact.enrichment_status = "enriched"

    db.flush()

    return contact


def mark_contact_enrichment_failed(
    db: Session,
    contact_id: int,
) -> ApolloProspectContact:
    contact = (
        db.query(ApolloProspectContact)
        .filter(ApolloProspectContact.id == contact_id)
        .one()
    )

    contact.enrichment_status = "failed"

    db.flush()

    return contact


def import_prospect_to_my_leads(
    db: Session,
    prospect_id: int,
    *,
    assigned_to: str,
) -> Lead:
    prospect = (
        db.query(ApolloProspect)
        .filter(ApolloProspect.id == prospect_id)
        .one()
    )

    if prospect.imported_lead_id is not None:
        existing_lead = (
            db.query(Lead)
            .filter(Lead.id == prospect.imported_lead_id)
            .one_or_none()
        )

        if existing_lead is not None:
            return existing_lead

    if prospect.review_status != "approved":
        raise ValueError(
            f"cannot import {prospect.review_status} prospect"
        )

    contact = (
        db.query(ApolloProspectContact)
        .filter(
            ApolloProspectContact.prospect_id == prospect.id,
            ApolloProspectContact.enrichment_status == "enriched",
            ApolloProspectContact.email.isnot(None),
            ApolloProspectContact.phone.isnot(None),
        )
        .order_by(ApolloProspectContact.id)
        .first()
    )

    lead = Lead(
        name=prospect.name,
        website=prospect.website_url,
        area=prospect.city,
        lead_type="agency",
        source="apollo",
        score=prospect.quality_score,
        status="new",
        assigned_to=assigned_to,
        contact_person=contact.name if contact else None,
        contact_person_role=contact.title if contact else None,
        email=contact.email if contact else None,
        phone=contact.phone if contact else None,
    )

    db.add(lead)
    db.flush()

    prospect.imported_lead_id = lead.id
    prospect.review_status = "imported"

    db.flush()

    return lead


def auto_import_contact_ready_prospect(
    db: Session,
    prospect_id: int,
    *,
    assigned_to: str,
) -> Lead:
    prospect = (
        db.query(ApolloProspect)
        .filter(ApolloProspect.id == prospect_id)
        .one()
    )
    if prospect.imported_lead_id is not None:
        existing = (
            db.query(Lead)
            .filter(Lead.id == prospect.imported_lead_id)
            .one_or_none()
        )
        if existing is not None:
            return existing

    contact_ready = next(
        (
            contact
            for contact in db.query(ApolloProspectContact)
            .filter(ApolloProspectContact.prospect_id == prospect.id)
            .all()
            if contact.email and contact.phone
        ),
        None,
    )
    if contact_ready is None:
        raise ValueError(
            "prospect must have both email and phone before automatic import"
        )

    prospect.review_status = "approved"
    db.flush()
    return import_prospect_to_my_leads(
        db,
        prospect.id,
        assigned_to=assigned_to,
    )
