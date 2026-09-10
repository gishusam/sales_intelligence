from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.apollo_prospect import ApolloProspect, ApolloProspectContact


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


def normalize_company_name(name: str | None) -> str | None:
    if not name:
        return None
    return " ".join(name.lower().split())


def upsert_prospect(
    db: Session,
    data: dict,
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

    for field in PROSPECT_UPDATE_FIELDS:
        if field in data:
            setattr(prospect, field, data[field])

    if "name" in data:
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

    if contact is None:
        contact = ApolloProspectContact(
            prospect_id=prospect.id,
            apollo_person_id=apollo_person_id,
        )
        db.add(contact)

    for field in CONTACT_UPDATE_FIELDS:
        if field in data:
            setattr(contact, field, data[field])

    db.flush()

    return contact



def persist_discovered_prospect(
    db: Session,
    data: dict,
) -> ApolloProspect:
    prospect = upsert_prospect(db, data)

    for contact_data in data.get("decision_makers", []):
        upsert_prospect_contact(
            db,
            prospect,
            contact_data,
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

    if prospect.review_status != "approved":
        raise ValueError(
            f"cannot import {prospect.review_status} prospect"
        )

    if prospect.imported_lead_id is not None:
        existing_lead = (
            db.query(Lead)
            .filter(Lead.id == prospect.imported_lead_id)
            .one_or_none()
        )

        if existing_lead is not None:
            return existing_lead

    contact = (
        db.query(ApolloProspectContact)
        .filter(ApolloProspectContact.prospect_id == prospect.id)
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
    db.flush()

    return lead
