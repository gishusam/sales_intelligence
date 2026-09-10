from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.lead import Lead
from app.models.apollo_prospect import (
    ApolloProspect,
    ApolloProspectContact,
)
from app.services.apollo_persistence import upsert_prospect


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


def test_upsert_prospect_reuses_apollo_organization_id_and_preserves_review_status():
    db = make_session()

    existing = ApolloProspect(
        apollo_organization_id="org-123",
        name="Old Company Name",
        normalized_name="old company name",
        review_status="approved",
        quality_score=40,
    )

    db.add(existing)
    db.commit()

    existing_id = existing.id

    prospect = upsert_prospect(
        db,
        {
            "apollo_organization_id": "org-123",
            "name": "Acme Property Management",
            "domain": "acme.co.ke",
            "website_url": "https://acme.co.ke",
            "linkedin_url": None,
            "employee_count": 48,
            "city": "Nairobi",
            "country": "Kenya",
            "industry": "property management",
            "keywords": ["tenant management"],
            "quality_score": 88,
            "quality_band": "high",
            "score_breakdown": {
                "company_fit": 38,
                "decision_maker_fit": 30,
                "market_fit": 15,
                "data_confidence": 5,
            },
            "score_reasons": [
                "Property management company",
            ],
        },
    )

    db.commit()

    assert prospect.id == existing_id
    assert prospect.name == "Acme Property Management"
    assert prospect.domain == "acme.co.ke"
    assert prospect.quality_score == 88

    # Existing workflow state must survive rediscovery.
    assert prospect.review_status == "approved"

    assert db.query(ApolloProspect).count() == 1


def test_upsert_prospect_reuses_domain_when_apollo_id_is_missing():
    db = make_session()

    existing = ApolloProspect(
        name="Acme Properties",
        normalized_name="acme properties",
        domain="Acme.CO.KE",
        review_status="rejected",
    )

    db.add(existing)
    db.commit()

    existing_id = existing.id

    prospect = upsert_prospect(
        db,
        {
            "apollo_organization_id": None,
            "name": "Acme Property Management",
            "domain": "acme.co.ke",
            "quality_score": 81,
            "quality_band": "high",
        },
    )

    db.commit()

    assert prospect.id == existing_id
    assert prospect.name == "Acme Property Management"
    assert prospect.quality_score == 81

    # Rediscovery must not reopen rejected prospects.
    assert prospect.review_status == "rejected"

    assert db.query(ApolloProspect).count() == 1


def test_upsert_prospect_reuses_normalized_company_name_as_last_fallback():
    db = make_session()

    existing = ApolloProspect(
        name="Acme Property Management",
        normalized_name="acme property management",
        review_status="pending_review",
    )

    db.add(existing)
    db.commit()

    existing_id = existing.id

    prospect = upsert_prospect(
        db,
        {
            "apollo_organization_id": None,
            "name": "  ACME   PROPERTY   MANAGEMENT  ",
            "domain": None,
            "quality_score": 76,
            "quality_band": "good",
        },
    )

    db.commit()

    assert prospect.id == existing_id
    assert prospect.quality_score == 76

    # Rediscovery must preserve workflow state.
    assert prospect.review_status == "pending_review"

    assert db.query(ApolloProspect).count() == 1


def test_upsert_prospect_promotes_apollo_id_after_domain_match():
    db = make_session()

    existing = ApolloProspect(
        apollo_organization_id=None,
        name="Acme Property Management",
        normalized_name="acme property management",
        domain="acme.co.ke",
        review_status="approved",
    )

    db.add(existing)
    db.commit()

    existing_id = existing.id

    prospect = upsert_prospect(
        db,
        {
            "apollo_organization_id": "org-999",
            "name": "Acme Property Management",
            "domain": "acme.co.ke",
            "quality_score": 90,
            "quality_band": "high",
        },
    )

    db.commit()

    assert prospect.id == existing_id
    assert prospect.apollo_organization_id == "org-999"
    assert prospect.review_status == "approved"
    assert db.query(ApolloProspect).count() == 1


def test_upsert_prospect_contact_reuses_apollo_person_id():
    from app.services.apollo_persistence import (
        upsert_prospect_contact,
    )

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-123",
        name="Acme Property Management",
        normalized_name="acme property management",
    )
    db.add(prospect)
    db.flush()

    existing = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-123",
        name="Jane Doe",
        title="Operations Manager",
    )
    db.add(existing)
    db.commit()

    existing_id = existing.id

    contact = upsert_prospect_contact(
        db,
        prospect,
        {
            "apollo_person_id": "person-123",
            "first_name": "Jane",
            "last_name": "Doe",
            "name": "Jane Doe",
            "title": "Head of Operations",
            "seniority": "head",
            "linkedin_url": "https://linkedin.com/in/jane-doe",
        },
    )

    db.commit()

    assert contact.id == existing_id
    assert contact.title == "Head of Operations"
    assert contact.seniority == "head"
    assert contact.linkedin_url == "https://linkedin.com/in/jane-doe"

    assert (
        db.query(ApolloProspectContact)
        .filter(ApolloProspectContact.prospect_id == prospect.id)
        .count()
        == 1
    )


def test_persist_discovered_prospect_saves_company_and_contacts():
    from app.services.apollo_persistence import (
        persist_discovered_prospect,
    )

    db = make_session()

    saved = persist_discovered_prospect(
        db,
        {
            "apollo_organization_id": "org-500",
            "name": "Nairobi Estates",
            "domain": "nairobiestates.co.ke",
            "city": "Nairobi",
            "country": "Kenya",
            "industry": "property management",
            "quality_score": 87,
            "quality_band": "high",
            "score_breakdown": {
                "company_fit": 37,
                "decision_maker_fit": 35,
                "market_fit": 15,
                "data_confidence": 0,
            },
            "score_reasons": [
                "Strong decision maker: Managing Director",
            ],
            "decision_makers": [
                {
                    "apollo_person_id": "person-500",
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "name": "Jane Doe",
                    "title": "Managing Director",
                    "seniority": "c_suite",
                    "linkedin_url": "https://linkedin.com/in/jane-doe",
                },
                {
                    "apollo_person_id": "person-501",
                    "first_name": "John",
                    "last_name": "Kamau",
                    "name": "John Kamau",
                    "title": "Operations Manager",
                    "seniority": "manager",
                    "linkedin_url": None,
                },
            ],
        },
    )

    db.commit()

    assert saved.review_status == "discovered"
    assert saved.quality_score == 87

    contacts = (
        db.query(ApolloProspectContact)
        .filter(ApolloProspectContact.prospect_id == saved.id)
        .all()
    )

    assert len(contacts) == 2
    assert {contact.apollo_person_id for contact in contacts} == {
        "person-500",
        "person-501",
    }


def test_move_prospect_to_review_queue_changes_enriched_to_pending_review():
    from app.services.apollo_persistence import (
        move_prospect_to_review_queue,
    )

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-review-1",
        name="Acme Property Management",
        normalized_name="acme property management",
        review_status="enriched",
    )

    db.add(prospect)
    db.commit()

    updated = move_prospect_to_review_queue(
        db,
        prospect.id,
    )

    db.commit()

    assert updated.id == prospect.id
    assert updated.review_status == "pending_review"


def test_move_prospect_to_review_queue_rejects_terminal_workflow_states():
    import pytest

    from app.services.apollo_persistence import (
        move_prospect_to_review_queue,
    )

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-review-locked",
        name="Locked Prospect",
        normalized_name="locked prospect",
        review_status="approved",
    )

    db.add(prospect)
    db.commit()

    with pytest.raises(
        ValueError,
        match="cannot move approved prospect to review queue",
    ):
        move_prospect_to_review_queue(
            db,
            prospect.id,
        )

    assert prospect.review_status == "approved"


def test_approve_prospect_changes_pending_review_to_approved():
    from app.services.apollo_persistence import approve_prospect

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-approve-1",
        name="Approve Me",
        normalized_name="approve me",
        review_status="pending_review",
    )

    db.add(prospect)
    db.commit()

    updated = approve_prospect(
        db,
        prospect.id,
    )

    db.commit()

    assert updated.id == prospect.id
    assert updated.review_status == "approved"


def test_approve_prospect_rejects_non_pending_review_state():
    import pytest

    from app.services.apollo_persistence import approve_prospect

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-approve-guard",
        name="Not Ready",
        normalized_name="not ready",
        review_status="discovered",
    )

    db.add(prospect)
    db.commit()

    with pytest.raises(
        ValueError,
        match="cannot approve discovered prospect",
    ):
        approve_prospect(
            db,
            prospect.id,
        )

    assert prospect.review_status == "discovered"


def test_reject_prospect_changes_pending_review_to_rejected():
    from app.services.apollo_persistence import reject_prospect

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-reject-1",
        name="Reject Me",
        normalized_name="reject me",
        review_status="pending_review",
    )

    db.add(prospect)
    db.commit()

    updated = reject_prospect(
        db,
        prospect.id,
    )

    db.commit()

    assert updated.id == prospect.id
    assert updated.review_status == "rejected"


def test_reject_prospect_rejects_non_pending_review_state():
    import pytest

    from app.services.apollo_persistence import reject_prospect

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-reject-guard",
        name="Not Ready For Rejection",
        normalized_name="not ready for rejection",
        review_status="discovered",
    )

    db.add(prospect)
    db.commit()

    with pytest.raises(
        ValueError,
        match="cannot reject discovered prospect",
    ):
        reject_prospect(
            db,
            prospect.id,
        )

    assert prospect.review_status == "discovered"


def test_apply_contact_enrichment_updates_contact_details():
    from app.services.apollo_persistence import apply_contact_enrichment

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-enrichment",
        name="Enrichment Company",
        normalized_name="enrichment company",
        review_status="approved",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-enrichment",
        first_name="Jane",
        last_name="Doe",
        name="Jane Doe",
        title="Head of Operations",
        enrichment_status="not_enriched",
    )

    db.add(contact)
    db.commit()

    updated = apply_contact_enrichment(
        db,
        contact.id,
        email="jane@example.com",
        phone="+254700000000",
    )

    db.commit()

    assert updated.id == contact.id
    assert updated.email == "jane@example.com"
    assert updated.phone == "+254700000000"
    assert updated.enrichment_status == "enriched"


def test_apply_contact_enrichment_preserves_existing_values_when_missing():
    from app.services.apollo_persistence import apply_contact_enrichment

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-partial-enrichment",
        name="Partial Enrichment Company",
        normalized_name="partial enrichment company",
        review_status="approved",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-partial-enrichment",
        first_name="Jane",
        last_name="Doe",
        name="Jane Doe",
        title="Head of Operations",
        email="existing@example.com",
        phone=None,
        enrichment_status="not_enriched",
    )

    db.add(contact)
    db.commit()

    updated = apply_contact_enrichment(
        db,
        contact.id,
        email=None,
        phone="+254711111111",
    )

    db.commit()

    assert updated.email == "existing@example.com"
    assert updated.phone == "+254711111111"
    assert updated.enrichment_status == "enriched"


def test_mark_contact_enrichment_failed_preserves_existing_contact_data():
    from app.services.apollo_persistence import mark_contact_enrichment_failed

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-enrichment-failure",
        name="Failed Enrichment Company",
        normalized_name="failed enrichment company",
        review_status="approved",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-enrichment-failure",
        first_name="John",
        last_name="Doe",
        name="John Doe",
        title="Operations Director",
        email="existing@example.com",
        phone="+254700000000",
        enrichment_status="not_enriched",
    )

    db.add(contact)
    db.commit()

    updated = mark_contact_enrichment_failed(
        db,
        contact.id,
    )

    db.commit()

    assert updated.id == contact.id
    assert updated.enrichment_status == "failed"
    assert updated.email == "existing@example.com"
    assert updated.phone == "+254700000000"


def test_apply_prospect_enrichment_updates_company_without_erasing_existing_values():
    from app.services import apollo_persistence

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="5e562b21b0b5190001a53287",
        name="Centum Real Estate",
        normalized_name="centum real estate",
        domain="existing-centum.co.ke",
        website_url=None,
        linkedin_url=(
            "https://www.linkedin.com/company/centum-re"
        ),
        employee_count=None,
        city=None,
        country=None,
        industry=None,
        review_status="discovered",
    )

    db.add(prospect)
    db.commit()

    updated = apollo_persistence.apply_prospect_enrichment(
        db,
        prospect.id,
        # Apollo omitted these: existing values must survive.
        domain=None,
        linkedin_url=None,

        # Apollo enrichment returned these richer values.
        website_url="https://centum.co.ke",
        employee_count=85,
        city="Nairobi",
        country="Kenya",
        industry="real estate",
    )

    db.commit()

    assert updated.id == prospect.id

    # None from Apollo must never destroy useful search data.
    assert updated.domain == "existing-centum.co.ke"
    assert updated.linkedin_url == (
        "https://www.linkedin.com/company/centum-re"
    )

    assert updated.website_url == "https://centum.co.ke"
    assert updated.employee_count == 85
    assert updated.city == "Nairobi"
    assert updated.country == "Kenya"
    assert updated.industry == "real estate"
