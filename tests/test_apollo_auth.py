from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models.lead import Lead
from app.models.apollo_prospect import (
    ApolloProspect,
    ApolloProspectContact,
)
from app.routers import apollo as apollo_router


def test_review_queue_requires_authentication():
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

    client = TestClient(app)

    response = client.get(
        "/api/apollo/prospects/review-queue"
    )

    assert response.status_code == 401


def test_apollo_health_remains_public():
    app = FastAPI()
    app.include_router(apollo_router.router)

    client = TestClient(app)

    response = client.get("/api/apollo/health")

    assert response.status_code != 401


def test_review_action_requires_authentication():
    app = FastAPI()
    app.include_router(apollo_router.router)

    client = TestClient(app)

    response = client.post("/api/apollo/prospects/1/review")

    assert response.status_code == 401


def test_approve_action_requires_authentication():
    app = FastAPI()
    app.include_router(apollo_router.router)

    client = TestClient(app)

    response = client.post("/api/apollo/prospects/1/approve")

    assert response.status_code == 401


def test_reject_action_requires_authentication():
    app = FastAPI()
    app.include_router(apollo_router.router)

    client = TestClient(app)

    response = client.post("/api/apollo/prospects/1/reject")

    assert response.status_code == 401


def test_import_action_requires_authentication():
    app = FastAPI()
    app.include_router(apollo_router.router)

    client = TestClient(app)

    response = client.post("/api/apollo/prospects/1/import")

    assert response.status_code == 401


def test_prospect_search_requires_authentication():
    app = FastAPI()
    app.include_router(apollo_router.router)

    client = TestClient(app)

    response = client.post(
        "/api/apollo/prospects/search",
        json={},
    )

    assert response.status_code == 401
