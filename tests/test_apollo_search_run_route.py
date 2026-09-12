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
from app.routers.apollo import router


def test_search_run_detail_reports_persisted_queue_progress():
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
        filters={"business_types": ["developers"], "locations": ["Nairobi"]},
        status="waiting_for_credits",
        found_count=10,
        processed_count=3,
        imported_count=2,
        no_contact_count=1,
        failed_count=0,
        queued_count=7,
        credit_status={"verified": True, "mode": "unified", "lead_credits_left": 4},
        billing_cycle_reset_at="2026-10-01",
        assigned_to="Jane Sales",
    )
    db.add(run)
    db.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=7, name="Jane Sales", email="jane@example.com", role="sales"
    )

    response = TestClient(app).get(f"/api/apollo/search-runs/{run.id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": run.id,
        "status": "waiting_for_credits",
        "filters": {"business_types": ["developers"], "locations": ["Nairobi"]},
        "found_count": 10,
        "processed_count": 3,
        "imported_count": 2,
        "no_contact_count": 1,
        "failed_count": 0,
        "queued_count": 7,
        "credit_status": {"verified": True, "mode": "unified", "lead_credits_left": 4},
        "billing_cycle_reset_at": "2026-10-01",
    }
