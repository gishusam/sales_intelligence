from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.lead import Lead
from app.models.apollo_prospect import (
    ApolloProspect,
    ApolloProspectContact,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")

    Base.metadata.create_all(
        engine,
        tables=[
            Lead.__table__,
            ApolloProspect.__table__,
            ApolloProspectContact.__table__,
        ],
    )

    Session = sessionmaker(bind=engine)
    return Session()


class FakeApolloClient:
    def __init__(self):
        self.person_ids = []

    def enrich_person(self, person_id):
        self.person_ids.append(person_id)

        return {
            "person": {
                "id": person_id,
                "email": "jane@example.com",
            }
        }


def test_enrich_prospect_contact_uses_apollo_id_and_persists_result():
    from app.services.apollo_enrichment import enrich_prospect_contact

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-enrich-service",
        name="Enrichment Service Company",
        normalized_name="enrichment service company",
        review_status="approved",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-service-123",
        name="Jane Doe",
        title="Managing Director",
        enrichment_status="not_enriched",
    )

    db.add(contact)
    db.commit()

    client = FakeApolloClient()

    updated = enrich_prospect_contact(
        db,
        client,
        contact.id,
    )

    db.commit()

    assert client.person_ids == ["person-service-123"]
    assert updated.id == contact.id
    assert updated.email == "jane@example.com"
    assert updated.enrichment_status == "enriched"


def test_enrich_prospect_contact_marks_failed_when_apollo_errors():
    import httpx

    from app.services.apollo_enrichment import enrich_prospect_contact

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-enrich-failure-service",
        name="Failure Service Company",
        normalized_name="failure service company",
        review_status="approved",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-failure-123",
        name="Jane Doe",
        title="Managing Director",
        email="existing@example.com",
        enrichment_status="not_enriched",
    )

    db.add(contact)
    db.commit()

    class FailingApolloClient:
        def enrich_person(self, person_id):
            raise httpx.HTTPError("Apollo enrichment failed")

    updated = enrich_prospect_contact(
        db,
        FailingApolloClient(),
        contact.id,
    )

    db.commit()

    assert updated.id == contact.id
    assert updated.email == "existing@example.com"
    assert updated.enrichment_status == "failed"


def test_enrich_prospect_contact_persists_real_person_details():
    from app.services.apollo_enrichment import enrich_prospect_contact

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="5e562b21b0b5190001a53287",
        name="Centum Real Estate",
        normalized_name="centum real estate",
        review_status="discovered",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="68527052713b92000135dac5",
        first_name="Kenneth",
        last_name=None,
        name="Kenneth Mb***e",
        title="Managing Director",
        linkedin_url=None,
        email="existing@example.com",
        enrichment_status="not_enriched",
    )

    db.add(contact)
    db.commit()

    class RealisticApolloClient:
        def enrich_person(self, person_id):
            assert person_id == "68527052713b92000135dac5"

            return {
                "person": {
                    "id": person_id,
                    "first_name": "Kenneth",
                    "last_name": "Mbae",
                    "name": "Kenneth Mbae",
                    "title": "Managing Director",
                    "linkedin_url": (
                        "http://www.linkedin.com/in/"
                        "kenneth-mbae-0b5b17a4"
                    ),
                    "email": None,
                    "email_status": "unavailable",
                }
            }

    updated = enrich_prospect_contact(
        db,
        RealisticApolloClient(),
        contact.id,
    )

    db.commit()

    assert updated.first_name == "Kenneth"
    assert updated.last_name == "Mbae"
    assert updated.name == "Kenneth Mbae"
    assert updated.title == "Managing Director"
    assert updated.linkedin_url == (
        "http://www.linkedin.com/in/"
        "kenneth-mbae-0b5b17a4"
    )

    # Apollo returning None must not erase useful existing data.
    assert updated.email == "existing@example.com"

    assert updated.enrichment_status == "enriched"
