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


def test_queue_message_uses_unique_recipient_step_key():
    from app.communications import campaign_execution_repository as repository

    db = FakeDb([Result(one=SimpleNamespace(id=801))])
    scheduled_for = datetime.now(timezone.utc)

    created = repository.queue_campaign_message(
        db=db,
        campaign_id=21,
        campaign_recipient_id=501,
        campaign_step_id=101,
        lead_id=1,
        template_id=9,
        sender_identity_id=11,
        sent_by=3,
        recipient_email="alice@example.test",
        recipient_name="Alice",
        subject="Hello Alpha Properties",
        body_text="Hi Alice",
        body_html=None,
        idempotency_key="campaign:21:recipient:501:step:101",
        scheduled_for=scheduled_for,
    )

    sql, params = db.calls[0]

    assert created is True
    assert "INSERT INTO email_messages" in sql
    assert "ON CONFLICT (idempotency_key)" in sql
    assert params["campaign_id"] == 21
    assert params["campaign_recipient_id"] == 501
    assert params["campaign_step_id"] == 101
    assert params["idempotency_key"] == (
        "campaign:21:recipient:501:step:101"
    )
    assert params["scheduled_for"] == scheduled_for


def test_cancel_campaign_cancels_pending_recipients_and_jobs():
    from app.communications import campaign_execution_repository as repository

    db = FakeDb([Result(), Result(), Result()])

    repository.cancel_campaign_and_pending_jobs(
        db=db,
        campaign_id=21,
        updated_by=3,
    )

    assert len(db.calls) == 3

    campaign_sql = db.calls[0][0]
    recipient_sql = db.calls[1][0]
    message_sql = db.calls[2][0]

    assert "UPDATE campaigns" in campaign_sql
    assert "status = 'cancelled'" in campaign_sql
    assert "UPDATE campaign_recipients" in recipient_sql
    assert "UPDATE email_messages" in message_sql
    assert "status = 'cancelled'" in message_sql
