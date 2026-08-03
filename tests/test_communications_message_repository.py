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


def test_create_message_persists_final_content_and_idempotency():
    from app.communications import message_repository

    row = SimpleNamespace(
        id=91,
        lead_id=41,
        template_id=9,
        sender_identity_id=11,
        sent_by=5,
        recipient_email="alice@example.test",
        recipient_name="Alice",
        subject="Edited subject",
        body_text="Edited body",
        body_html=None,
        message_type="manual",
        status="queued",
        idempotency_key="lead-41-manual-001",
        provider_message_id=None,
        attachment_name=None,
        attachment_content_type=None,
        attachment_size=None,
        follow_up_date=None,
        error_message=None,
        sent_at=None,
        created_at=None,
        updated_at=None,
    )
    db = FakeDb([Result(one=row)])

    created = message_repository.create_queued_message(
        db=db,
        values={
            "lead_id": 41,
            "template_id": 9,
            "sender_identity_id": 11,
            "sent_by": 5,
            "recipient_email": "alice@example.test",
            "recipient_name": "Alice",
            "subject": "Edited subject",
            "body_text": "Edited body",
            "body_html": None,
            "message_type": "manual",
            "idempotency_key": "lead-41-manual-001",
            "attachment_name": None,
            "attachment_content_type": None,
            "attachment_size": None,
            "follow_up_date": None,
        },
    )

    sql, params = db.calls[0]
    assert created["id"] == 91
    assert "INSERT INTO email_messages" in sql
    assert params["subject"] == "Edited subject"
    assert params["body_text"] == "Edited body"
    assert params["idempotency_key"] == "lead-41-manual-001"


def test_suppression_lookup_normalizes_email():
    from app.communications import message_repository

    db = FakeDb(
        [Result(one=SimpleNamespace(is_suppressed=True))]
    )
    result = message_repository.is_recipient_suppressed(
        db=db,
        email_address=" Alice@Example.Test ",
    )

    sql, params = db.calls[0]
    assert result is True
    assert "LOWER(email_address)" in sql
    assert params["email_address"] == "alice@example.test"
