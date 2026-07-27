import sys
from pathlib import Path

from sqlalchemy import event, text


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def test_api_engine_opens_a_fresh_dbapi_connection_for_each_checkout(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://test:test@localhost:5432/test",
    )
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret")

    from app import database

    engine = database.build_engine("sqlite://")
    physical_connects = 0

    @event.listens_for(engine, "connect")
    def count_connects(*_args):
        nonlocal physical_connects
        physical_connects += 1

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    assert physical_connects == 2
