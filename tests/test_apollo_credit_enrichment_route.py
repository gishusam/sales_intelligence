from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import CurrentUser, get_current_user
from app.database import Base, get_db
from app.models.apollo_prospect import ApolloProspect, ApolloProspectContact
from app.models.apollo_search_run import ApolloSearchRun, ApolloSearchRunProspect
from app.models.lead import Lead
from app.routers import apollo as apollo_router


def test_enrich_search_run_route_returns_resumable_credit_status(monkeypatch):
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
            ApolloSearchRun.__table__,
            ApolloSearchRunProspect.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    run = ApolloSearchRun(
        filters={"locations": ["Nairobi"]},
        assigned_to="Jane Sales",
        status="queued",
        found_count=1,
        queued_count=1,
    )
    prospect = ApolloProspect(name="Acme", review_status="discovered")
    db.add_all([run, prospect])
    db.flush()
    db.add(ApolloSearchRunProspect(search_run_id=run.id, prospect_id=prospect.id))
    db.commit()

    class FakeApolloClient:
        def __init__(self, api_key):
            pass

        def get_credit_usage(self):
            return {
                "credit_usage_stats": {
                    "lead_credit": {"limit": 100, "consumed": 96, "left_over": 4}
                },
                "current_credit_cycle": {"end_date": "2026-10-01"},
            }

    monkeypatch.setattr(apollo_router, "ApolloClient", FakeApolloClient)
    monkeypatch.setattr(apollo_router.settings, "APOLLO_API_KEY", "test-key")
    monkeypatch.setattr(apollo_router.settings, "APOLLO_WEBHOOK_SECRET", "secret")

    app = FastAPI()
    app.include_router(apollo_router.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=7, name="Jane Sales", email="jane@example.com", role="sales"
    )

    response = TestClient(app).post(f"/api/apollo/search-runs/{run.id}/enrich")

    assert response.status_code == 200
    assert response.json()["status"] == "waiting_for_credits"
    assert response.json()["queued_count"] == 1
    assert response.json()["credit_status"]["verified"] is True
    assert response.json()["billing_cycle_reset_at"] == "2026-10-01"
