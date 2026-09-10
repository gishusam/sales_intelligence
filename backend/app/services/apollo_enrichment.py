import httpx

from sqlalchemy.orm import Session

from app.models.apollo_prospect import ApolloProspectContact
from app.services.apollo_persistence import (
    apply_contact_enrichment,
    mark_contact_enrichment_failed,
)


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
