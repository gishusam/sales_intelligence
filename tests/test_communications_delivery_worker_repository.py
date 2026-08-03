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
        self.commits = 0

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return self.responses.pop(0)

    def commit(self):
        self.commits += 1


def test_claim_due_messages_uses_skip_locked_and_attempt_counter():
    from app.communications import delivery_worker_repository as repository

    row = SimpleNamespace(
        id=801,
        campaign_id=21,
        campaign_recipient_id=501,
        campaign_step_id=101,
        lead_id=1,
        template_id=9,
        sender_identity_id=11,
        sent_by=3,
        recipient_email="alice@example.test",
        recipient_name="Alice",
        subject="Stored subject",
        body_text="Stored body",
        body_html=None,
        message_type="campaign",
        status="processing",
        idempotency_key="campaign:21:recipient:501:step:101",
        attempt_count=1,
        scheduled_for=datetime.now(timezone.utc),
    )
    db = FakeDb([Result(rows=[row])])

    claimed = repository.claim_due_messages(
        db=db,
        limit=25,
        worker_id="worker-1",
        now=datetime.now(timezone.utc),
    )

    sql, params = db.calls[0]
    assert len(claimed) == 1
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "message_type = 'campaign'" in sql
    assert "attempt_count = COALESCE" in sql
    assert "status = 'processing'" in sql
    assert params["limit"] == 25
    assert params["worker_id"] == "worker-1"
    assert db.commits == 1


def test_next_step_queue_is_idempotent():
    from app.communications import delivery_worker_repository as repository

    db = FakeDb([Result(one=SimpleNamespace(id=802))])
    created = repository.queue_next_step_message(
        db=db,
        campaign_id=21,
        campaign_recipient_id=501,
        campaign_step_id=102,
        lead_id=1,
        template_id=10,
        sender_identity_id=11,
        sent_by=3,
        recipient_email="alice@example.test",
        recipient_name="Alice",
        subject="Following up",
        body_text="Hi Alice",
        body_html=None,
        idempotency_key="campaign:21:recipient:501:step:102",
        scheduled_for=datetime.now(timezone.utc),
    )

    sql, params = db.calls[0]
    assert created is True
    assert "ON CONFLICT (idempotency_key)" in sql
    assert "DO NOTHING" in sql
    assert params["campaign_step_id"] == 102
