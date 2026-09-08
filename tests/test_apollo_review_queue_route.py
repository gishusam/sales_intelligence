from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import CurrentUser, get_current_user
from app.database import Base, get_db
from app.models.lead import Lead
from app.models.apollo_prospect import (
    ApolloProspect,
    ApolloProspectContact,
)
from app.routers import apollo as apollo_router


def override_sales_user():
    return CurrentUser(
        id=7,
        name="Jane Sales",
        email="jane@example.com",
        role="sales",
    )


def test_move_prospect_to_review_queue_endpoint():
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
        apollo_organization_id="org-review-http",
        name="Acme Property Management",
        normalized_name="acme property management",
        review_status="discovered",
    )
    db.add(prospect)
    db.commit()

    prospect_id = prospect.id

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app = FastAPI()
    app.include_router(apollo_router.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_sales_user

    client = TestClient(app)

    response = client.post(
        f"/api/apollo/prospects/{prospect_id}/review"
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": prospect_id,
        "review_status": "pending_review",
    }

    db.refresh(prospect)
    assert prospect.review_status == "pending_review"


def test_review_endpoint_returns_conflict_for_approved_prospect():
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
        apollo_organization_id="org-approved-http",
        name="Approved Prospect",
        normalized_name="approved prospect",
        review_status="approved",
    )
    db.add(prospect)
    db.commit()

    prospect_id = prospect.id

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app = FastAPI()
    app.include_router(apollo_router.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_sales_user

    client = TestClient(app)

    response = client.post(
        f"/api/apollo/prospects/{prospect_id}/review"
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "cannot move approved prospect to review queue"
    }

    db.refresh(prospect)
    assert prospect.review_status == "approved"


def test_review_queue_lists_only_pending_review_prospects():
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

    pending = ApolloProspect(
        apollo_organization_id="org-pending-queue",
        name="Pending Property Managers",
        normalized_name="pending property managers",
        domain="pending.co.ke",
        quality_score=88,
        quality_band="high",
        review_status="pending_review",
    )

    discovered = ApolloProspect(
        apollo_organization_id="org-discovered-queue",
        name="Discovered Property Managers",
        normalized_name="discovered property managers",
        quality_score=72,
        review_status="discovered",
    )

    approved = ApolloProspect(
        apollo_organization_id="org-approved-queue",
        name="Approved Property Managers",
        normalized_name="approved property managers",
        quality_score=90,
        review_status="approved",
    )

    db.add_all([
        pending,
        discovered,
        approved,
    ])
    db.commit()

    pending_id = pending.id

    def override_get_db():
        try:
            yield db
        finally:
            pass

    def override_get_current_user():
        return CurrentUser(
            id=7,
            name="Jane Sales",
            email="jane@example.com",
            role="sales",
        )

    app = FastAPI()
    app.include_router(apollo_router.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_sales_user
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(app)

    response = client.get(
        "/api/apollo/prospects/review-queue"
    )

    assert response.status_code == 200

    assert response.json() == {
        "prospects": [
            {
                "id": pending_id,
                "name": "Pending Property Managers",
                "domain": "pending.co.ke",
                "quality_score": 88,
                "quality_band": "high",
                "review_status": "pending_review",
            }
        ]
    }


def test_approve_prospect_endpoint():
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
        apollo_organization_id="org-approve-http",
        name="Approve HTTP Prospect",
        normalized_name="approve http prospect",
        review_status="pending_review",
    )

    db.add(prospect)
    db.commit()

    prospect_id = prospect.id

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app = FastAPI()
    app.include_router(apollo_router.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_sales_user

    client = TestClient(app)

    response = client.post(
        f"/api/apollo/prospects/{prospect_id}/approve"
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": prospect_id,
        "review_status": "approved",
    }

    db.refresh(prospect)
    assert prospect.review_status == "approved"


def test_approve_endpoint_returns_conflict_for_non_pending_prospect():
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
        apollo_organization_id="org-approve-conflict-http",
        name="Approve Conflict Prospect",
        normalized_name="approve conflict prospect",
        review_status="discovered",
    )

    db.add(prospect)
    db.commit()

    prospect_id = prospect.id

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app = FastAPI()
    app.include_router(apollo_router.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_sales_user

    client = TestClient(app)

    response = client.post(
        f"/api/apollo/prospects/{prospect_id}/approve"
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "cannot approve discovered prospect"
    }

    db.refresh(prospect)
    assert prospect.review_status == "discovered"


def test_reject_prospect_endpoint():
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
        apollo_organization_id="org-reject-http",
        name="Reject HTTP Prospect",
        normalized_name="reject http prospect",
        review_status="pending_review",
    )

    db.add(prospect)
    db.commit()

    prospect_id = prospect.id

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app = FastAPI()
    app.include_router(apollo_router.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_sales_user

    client = TestClient(app)

    response = client.post(
        f"/api/apollo/prospects/{prospect_id}/reject"
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": prospect_id,
        "review_status": "rejected",
    }

    db.refresh(prospect)
    assert prospect.review_status == "rejected"


def test_reject_endpoint_returns_conflict_for_non_pending_prospect():
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
        apollo_organization_id="org-reject-conflict-http",
        name="Reject Conflict Prospect",
        normalized_name="reject conflict prospect",
        review_status="approved",
    )

    db.add(prospect)
    db.commit()

    prospect_id = prospect.id

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app = FastAPI()
    app.include_router(apollo_router.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_sales_user

    client = TestClient(app)

    response = client.post(
        f"/api/apollo/prospects/{prospect_id}/reject"
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "cannot reject approved prospect"
    }

    db.refresh(prospect)
    assert prospect.review_status == "approved"


def test_review_endpoint_returns_not_found_for_missing_prospect():
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

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app = FastAPI()
    app.include_router(apollo_router.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_sales_user

    client = TestClient(
        app,
        raise_server_exceptions=False,
    )

    response = client.post(
        "/api/apollo/prospects/999/review"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Apollo prospect not found"
    }


def test_approve_endpoint_returns_not_found_for_missing_prospect():
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

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app = FastAPI()
    app.include_router(apollo_router.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_sales_user

    client = TestClient(
        app,
        raise_server_exceptions=False,
    )

    response = client.post(
        "/api/apollo/prospects/999/approve"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Apollo prospect not found"
    }


def test_reject_endpoint_returns_not_found_for_missing_prospect():
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

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app = FastAPI()
    app.include_router(apollo_router.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_sales_user

    client = TestClient(
        app,
        raise_server_exceptions=False,
    )

    response = client.post(
        "/api/apollo/prospects/999/reject"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Apollo prospect not found"
    }
