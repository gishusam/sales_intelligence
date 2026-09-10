import httpx

from sqlalchemy.orm import Session

from app.models.apollo_prospect import (
    ApolloProspect,
    ApolloProspectContact,
)
from app.services.apollo_persistence import (
    apply_contact_enrichment,
    apply_prospect_enrichment,
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
