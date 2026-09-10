from app.services.apollo_persistence import import_prospect_to_my_leads
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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


def test_import_approved_prospect_creates_lead_assigned_to_sales_rep():
    from app.services.apollo_persistence import import_prospect_to_my_leads

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-import-1",
        name="Acme Property Management",
        normalized_name="acme property management",
        domain="acme.co.ke",
        website_url="https://acme.co.ke",
        city="Nairobi",
        quality_score=88,
        quality_band="high",
        review_status="approved",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-import-1",
        name="Jane Doe",
        title="Managing Director",
        email="jane@acme.co.ke",
        phone="+254700000000",
        enrichment_status="enriched",
    )

    db.add(contact)
    db.commit()

    lead = import_prospect_to_my_leads(
        db,
        prospect.id,
        assigned_to="Sales Rep",
    )

    db.commit()
    db.refresh(prospect)

    assert lead.name == "Acme Property Management"
    assert lead.website == "https://acme.co.ke"
    assert lead.area == "Nairobi"

    assert lead.contact_person == "Jane Doe"
    assert lead.contact_person_role == "Managing Director"
    assert lead.email == "jane@acme.co.ke"
    assert lead.phone == "+254700000000"

    assert lead.source == "apollo"
    assert lead.lead_type == "agency"
    assert lead.score == 88
    assert lead.status == "new"
    assert lead.assigned_to == "Sales Rep"

    assert prospect.imported_lead_id == lead.id
    assert db.query(Lead).count() == 1


def test_import_rejects_non_approved_prospect():
    from app.services.apollo_persistence import import_prospect_to_my_leads

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-import-not-approved",
        name="Not Approved Property Managers",
        normalized_name="not approved property managers",
        review_status="pending_review",
    )

    db.add(prospect)
    db.commit()

    try:
        import_prospect_to_my_leads(
            db,
            prospect.id,
            assigned_to="Sales Rep",
        )
    except ValueError as exc:
        assert str(exc) == "cannot import pending_review prospect"
    else:
        raise AssertionError("Expected ValueError")

    assert db.query(Lead).count() == 0

    db.refresh(prospect)
    assert prospect.imported_lead_id is None


def test_repeat_import_reuses_existing_lead():
    from app.services.apollo_persistence import import_prospect_to_my_leads

    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-import-repeat",
        name="Repeat Property Management",
        normalized_name="repeat property management",
        review_status="approved",
        quality_score=91,
    )

    db.add(prospect)
    db.commit()

    first_lead = import_prospect_to_my_leads(
        db,
        prospect.id,
        assigned_to="Sales Rep",
    )
    db.commit()

    second_lead = import_prospect_to_my_leads(
        db,
        prospect.id,
        assigned_to="Sales Rep",
    )
    db.commit()

    assert second_lead.id == first_lead.id
    assert db.query(Lead).count() == 1

    db.refresh(prospect)
    assert prospect.imported_lead_id == first_lead.id


def test_import_recovers_from_stale_imported_lead_id():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(
        engine,
        tables=[
            Lead.__table__,
            ApolloProspect.__table__,
            ApolloProspectContact.__table__,
        ],
    )

    Session = sessionmaker(bind=engine)
    db = Session()

    prospect = ApolloProspect(
        apollo_organization_id="org-stale-import",
        name="Stale Link Property Managers",
        normalized_name="stale link property managers",
        website_url="https://stale.example.com",
        city="Nairobi",
        quality_score=86,
        quality_band="high",
        review_status="approved",
        imported_lead_id=999,
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-stale-import",
        name="Jane Manager",
        title="Managing Director",
        email="jane@stale.example.com",
        phone="+254700000001",
        enrichment_status="enriched",
    )

    db.add(contact)
    db.commit()

    prospect_id = prospect.id

    lead = import_prospect_to_my_leads(
        db,
        prospect_id,
        assigned_to="Jane Sales",
    )

    db.commit()
    db.refresh(prospect)

    assert lead.id != 999
    assert prospect.imported_lead_id == lead.id

    assert lead.name == "Stale Link Property Managers"
    assert lead.assigned_to == "Jane Sales"
    assert lead.source == "apollo"

    assert db.query(Lead).count() == 1


def test_successful_import_marks_prospect_imported_and_repeat_reuses_lead():
    db = make_session()

    prospect = ApolloProspect(
        apollo_organization_id="org-import-state",
        name="Import State Property Managers",
        normalized_name="import state property managers",
        website_url="https://import-state.example.com",
        city="Nairobi",
        quality_score=89,
        quality_band="high",
        review_status="approved",
    )

    db.add(prospect)
    db.flush()

    contact = ApolloProspectContact(
        prospect_id=prospect.id,
        apollo_person_id="person-import-state",
        name="Jane Manager",
        title="Managing Director",
        email="jane@example.com",
        enrichment_status="enriched",
    )

    db.add(contact)
    db.commit()

    first = import_prospect_to_my_leads(
        db,
        prospect.id,
        assigned_to="Sales Rep",
    )

    db.commit()
    db.refresh(prospect)

    assert prospect.review_status == "imported"
    assert prospect.imported_lead_id == first.id
    assert db.query(Lead).count() == 1

    second = import_prospect_to_my_leads(
        db,
        prospect.id,
        assigned_to="Sales Rep",
    )

    db.commit()

    assert second.id == first.id
    assert db.query(Lead).count() == 1

    db.refresh(prospect)
    assert prospect.review_status == "imported"
    assert prospect.imported_lead_id == first.id
