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

from app.auth import CurrentUser


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
        self.rollbacks = 0

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return self.responses.pop(0)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def rule_row():
    return SimpleNamespace(
        id=31,
        name="Seven-day follow-up",
        trigger_type="inactivity_followup",
        template_id=10,
        sender_identity_id=11,
        inactivity_days=7,
        max_drafts_per_lead=1,
        is_active=False,
        created_by=3,
        updated_by=3,
        created_at=None,
        updated_at=None,
    )


def test_create_rule_persists_inactive_default():
    from app.communications import automation_repository
    from app.communications.automation_schemas import AutomationRuleCreate

    db = FakeDb([Result(one=rule_row())])

    created = automation_repository.create_rule(
        db=db,
        payload=AutomationRuleCreate(
            name="Seven-day follow-up",
            template_id=10,
            sender_identity_id=11,
            inactivity_days=7,
        ),
        user=CurrentUser(
            id=3,
            name="Admin User",
            email="admin@example.test",
            role="admin",
        ),
    )

    sql, params = db.calls[0]

    assert created["is_active"] is False
    assert "INSERT INTO automation_rules" in sql
    assert params["is_active"] is False
    assert db.commits == 1


def test_automation_draft_is_reviewable_and_idempotent():
    from app.communications import automation_repository

    db = FakeDb([Result(one=SimpleNamespace(id=901))])

    message_id = automation_repository.create_automation_draft(
        db=db,
        automation_rule_id=31,
        lead_id=41,
        template_id=10,
        sender_identity_id=11,
        sent_by=3,
        recipient_email="alice@example.test",
        recipient_name="Alice",
        subject="Following up",
        body_text="Hi Alice",
        body_html=None,
        idempotency_key="automation:31:lead:41:2026-08-03",
        status="draft",
        message_type="automation",
    )

    sql, params = db.calls[0]

    assert message_id == 901
    assert "INSERT INTO email_messages" in sql
    assert "'draft'" in sql
    assert "'automation'" in sql
    assert "ON CONFLICT (idempotency_key)" in sql
    assert params["idempotency_key"] == (
        "automation:31:lead:41:2026-08-03"
    )
