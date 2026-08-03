import os
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.test")


class Result:
    def __init__(self, *, one=None, rows=None):
        self._one = one
        self._rows = rows or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class FakeDb:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return self.responses.pop(0)


def test_create_event_uses_provider_event_identity():
    from app.communications import provider_event_repository as repository

    db = FakeDb([Result(one=SimpleNamespace(id=1001))])

    event_id = repository.create_event(
        db=db,
        provider="generic",
        provider_event_id="evt-001",
        email_message_id=901,
        provider_message_id="provider-message-123",
        event_type="delivered",
        recipient_email="alice@example.test",
        bounce_type=None,
        reason=None,
        url=None,
        occurred_at=datetime.now(timezone.utc),
        payload={"event": "delivered"},
        signature_verified=True,
        status="processed",
    )

    sql, params = db.calls[0]

    assert event_id == 1001
    assert "INSERT INTO email_events" in sql
    assert params["provider"] == "generic"
    assert params["provider_event_id"] == "evt-001"
    assert params["signature_verified"] is True


def test_message_event_update_supports_engagement_counters():
    from app.communications import provider_event_repository as repository

    db = FakeDb([Result()])

    repository.apply_message_event(
        db=db,
        message_id=901,
        event_type="clicked",
        occurred_at=datetime.now(timezone.utc),
        bounce_type=None,
        reason=None,
    )

    sql = db.calls[0][0]

    assert "click_count = COALESCE(click_count, 0) + 1" in sql
    assert "clicked_at = COALESCE" in sql
