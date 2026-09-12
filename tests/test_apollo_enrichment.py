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


def test_enrich_prospect_enriches_company_best_contact_and_rescores():
    from app.services.apollo_enrichment import enrich_prospect

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="5e562b21b0b5190001a53287",
        name="Centum Real Estate",
        normalized_name="centum real estate",
        linkedin_url=(
            "https://www.linkedin.com/company/centum-re"
        ),
        quality_score=5,
        quality_band="weak",
        review_status="discovered",
    )

    db.add(prospect)
    db.flush()

    operations_manager = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-operations",
        name="Operations Manager",
        title="Operations Manager",
        seniority="manager",
        enrichment_status="not_enriched",
    )

    managing_director = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="68527052713b92000135dac5",
        first_name="Kenneth",
        name="Kenneth Mb***e",
        title="Managing Director",
        seniority="c_suite",
        enrichment_status="not_enriched",
    )

    db.add_all([
        operations_manager,
        managing_director,
    ])
    db.commit()

    class FakeApolloClient:
        def __init__(self):
            self.organization_calls = []
            self.person_ids = []

        def enrich_organization(
            self,
            *,
            domain=None,
            linkedin_url=None,
            website=None,
            name=None,
        ):
            self.organization_calls.append({
                "domain": domain,
                "linkedin_url": linkedin_url,
                "website": website,
                "name": name,
            })

            return {
                "organization": {
                    "id": "5e562b21b0b5190001a53287",
                    "name": "Centum Real Estate",
                    "primary_domain": "centum.co.ke",
                    "website_url": "https://centum.co.ke",
                    "linkedin_url": (
                        "https://www.linkedin.com/company/"
                        "centum-re"
                    ),
                    "estimated_num_employees": 85,
                    "city": "Nairobi",
                    "country": "Kenya",
                    "industry": "real estate",
                }
            }

        def enrich_person(self, person_id):
            self.person_ids.append(person_id)

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
                }
            }

    client = FakeApolloClient()

    updated = enrich_prospect(
        db,
        client,
        prospect.id,
    )

    db.commit()

    assert client.organization_calls == [{
        "domain": None,
        "linkedin_url": (
            "https://www.linkedin.com/company/centum-re"
        ),
        "website": None,
        "name": "Centum Real Estate",
    }]

    # Only the strongest decision-maker should consume
    # a person-enrichment request.
    assert client.person_ids == [
        "68527052713b92000135dac5"
    ]

    db.refresh(prospect)
    db.refresh(operations_manager)
    db.refresh(managing_director)

    assert updated.domain == "centum.co.ke"
    assert updated.website_url == "https://centum.co.ke"
    assert updated.employee_count == 85
    assert updated.city == "Nairobi"
    assert updated.country == "Kenya"
    assert updated.industry == "real estate"

    assert managing_director.name == "Kenneth Mbae"
    assert managing_director.linkedin_url == (
        "http://www.linkedin.com/in/"
        "kenneth-mbae-0b5b17a4"
    )
    assert managing_director.enrichment_status == "enriched"

    # We deliberately did not spend a credit enriching
    # the weaker contact.
    assert operations_manager.enrichment_status == (
        "not_enriched"
    )

    # Recalculated using enriched firmographic/contact data.
    assert updated.quality_score == 75
    assert updated.quality_band == "good"

    assert updated.review_status == "enriched"


def test_contact_enrichment_webhook_persists_email_and_phone():
    from app.services.apollo_enrichment import (
        apply_contact_details_webhook,
    )

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="5e562b21b0b5190001a53287",
        name="Centum Real Estate",
        normalized_name="centum real estate",
        review_status="enriched",
    )

    db.add(prospect)
    db.flush()

    kenneth = ApolloProspectContact(
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

    db.add(kenneth)
    db.commit()

    payload = {
        "status": "success",
        "target_fields": [
            "emails",
            "phone_numbers",
        ],
        "records_enriched": 1,
        "email_records_enriched": 1,
        "mobile_records_enriched": 1,
        "request_id": "request-123",
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
                        "raw_number": "0712 345 678",
                        "sanitized_number": "+254712345678",
                        "status_cd": "valid_number",
                        "type_cd": "mobile",
                    }
                ],
            }
        ],
    }

    updated_count = apply_contact_details_webhook(
        db,
        payload,
    )

    db.commit()
    db.refresh(kenneth)

    assert updated_count == 1

    assert kenneth.email == "kenneth@centum.co.ke"
    assert kenneth.phone == "+254712345678"

    assert kenneth.name == "Kenneth Mbae"
    assert kenneth.title == "Managing Director"
    assert kenneth.enrichment_status == "enriched"


def test_contact_webhook_marks_contact_complete_with_email_and_phone():
    from app.services.apollo_enrichment import (
        apply_contact_details_webhook,
    )

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-contact-complete",
        name="Westlands Property Managers",
        normalized_name="westlands property managers",
        review_status="enriched",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-contact-complete",
        name="Jane Manager",
        title="Managing Director",
        enrichment_status="enriched",
        contact_enrichment_status="pending",
    )

    db.add(contact)
    db.commit()

    apply_contact_details_webhook(
        db,
        {
            "people": [
                {
                    "id": "person-contact-complete",
                    "emails": [
                        {
                            "email": "jane@example.com",
                        }
                    ],
                    "phone_numbers": [
                        {
                            "sanitized_number": "+254700000000",
                        }
                    ],
                }
            ],
        },
    )

    db.commit()
    db.refresh(contact)

    assert contact.email == "jane@example.com"
    assert contact.phone == "+254700000000"
    assert contact.contact_enrichment_status == "complete"


def test_contact_webhook_marks_not_found_when_no_contact_details():
    from app.services.apollo_enrichment import (
        apply_contact_details_webhook,
    )

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-no-contact-result",
        name="No Contact Property Managers",
        normalized_name="no contact property managers",
        review_status="enriched",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-no-contact-result",
        name="Jane Manager",
        title="Managing Director",
        enrichment_status="enriched",
        contact_enrichment_status="pending",
    )

    db.add(contact)
    db.commit()

    updated = apply_contact_details_webhook(
        db,
        {
            "people": [
                {
                    "id": "person-no-contact-result",
                    "emails": [],
                    "phone_numbers": [],
                }
            ],
        },
    )

    db.commit()
    db.refresh(contact)

    assert updated == 1
    assert contact.email is None
    assert contact.phone is None
    assert contact.contact_enrichment_status == "not_found"


def test_request_contact_enrichment_accepts_native_phone_pending_response():
    from app.services.apollo_enrichment import (
        request_contact_enrichment,
    )

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-native-phone",
        name="Native Phone Property Managers",
        normalized_name="native phone property managers",
        review_status="enriched",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-native-phone",
        first_name="Jane",
        last_name="Manager",
        name="Jane Manager",
        title="Property Manager",
        seniority="manager",
        email="jane@example.com",
        phone=None,
        enrichment_status="enriched",
        contact_enrichment_status="not_requested",
    )

    db.add(contact)
    db.commit()

    class FakeClient:
        def enrich_contact_details(
            self,
            *,
            person_id,
            webhook_url,
            first_name=None,
            last_name=None,
            linkedin_url=None,
        ):
            assert person_id == "person-native-phone"
            assert webhook_url == "https://example.com/apollo-webhook"

            return {
                "person": {
                    "id": person_id,
                },
                "phone_enrichment": {
                    "status": "pending",
                    "request_id": "phone-native-123",
                },
                "request_id": -987654321,
            }

    updated = request_contact_enrichment(
        db,
        FakeClient(),
        prospect.id,
        webhook_url="https://example.com/apollo-webhook",
    )

    db.commit()
    db.refresh(contact)

    assert updated.id == contact.id
    assert contact.contact_enrichment_status == "pending"
    assert contact.contact_enrichment_request_id == "-987654321"
