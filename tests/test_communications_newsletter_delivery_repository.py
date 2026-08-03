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


def test_newsletter_queue_is_idempotent():
    from app.communications import newsletter_delivery_repository as repository

    db = FakeDb([Result(one=SimpleNamespace(id=901))])
    scheduled_for = datetime.now(timezone.utc)

    created = repository.queue_newsletter_message(
        db=db,
        newsletter_id=71,
        newsletter_recipient_id=801,
        lead_id=1,
        sender_identity_id=11,
        sent_by=3,
        recipient_email="alice@example.test",
        recipient_name="Alice",
        subject="August brief",
        body_text="Hello Alice",
        body_html="<p>Hello Alice</p>",
        idempotency_key="newsletter:71:recipient:801",
        scheduled_for=scheduled_for,
    )

    sql, params = db.calls[0]

    assert created is True
    assert "INSERT INTO email_messages" in sql
    assert "'newsletter'" in sql
    assert "ON CONFLICT (idempotency_key)" in sql
    assert params["newsletter_recipient_id"] == 801
    assert params["scheduled_for"] == scheduled_for


def test_cancel_newsletter_updates_three_domains():
    from app.communications import newsletter_delivery_repository as repository

    db = FakeDb([Result(), Result(), Result()])

    repository.cancel_newsletter_and_jobs(
        db=db,
        newsletter_id=71,
        updated_by=3,
    )

    assert len(db.calls) == 3
    assert "UPDATE newsletter_drafts" in db.calls[0][0]
    assert "UPDATE newsletter_recipients" in db.calls[1][0]
    assert "UPDATE email_messages" in db.calls[2][0]
