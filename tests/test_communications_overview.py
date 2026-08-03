import os
from types import SimpleNamespace

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.test")


class Result:
    def __init__(self, *, one=None):
        self._one = one

    def fetchone(self):
        return self._one


class FakeDb:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return self.responses.pop(0)


def test_overview_returns_operational_totals():
    from app.communications import communications_overview_repository

    row = SimpleNamespace(
        total_messages=120,
        queued_messages=7,
        processing_messages=2,
        sent_messages=90,
        delivered_messages=80,
        failed_messages=5,
        dead_letter_messages=1,
        bounced_messages=4,
        complained_messages=1,
        unsubscribed_messages=3,
        opens=40,
        clicks=15,
        active_campaigns=2,
        active_newsletters=1,
        suppressed_contacts=8,
    )
    db = FakeDb([Result(one=row)])

    result = communications_overview_repository.get_overview(db=db)
    sql = db.calls[0][0]

    assert result["total_messages"] == 120
    assert result["dead_letter_messages"] == 1
    assert result["suppressed_contacts"] == 8
    assert "FROM email_messages" in sql
    assert "FROM campaigns" in sql
    assert "FROM newsletter_drafts" in sql
    assert "FROM suppression_list" in sql


def test_main_mounts_overview_router():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert (
        "from app.routers import communication_overview "
        "as communication_overview_router"
    ) in source
    assert (
        "app.include_router("
        "communication_overview_router.router"
        ")"
    ) in source
