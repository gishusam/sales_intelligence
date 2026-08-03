import os
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


def campaign_row(**overrides):
    values = {
        "id": 21,
        "name": "Kilimani agency outreach",
        "description": "Draft campaign",
        "campaign_type": "cold",
        "sender_identity_id": 11,
        "status": "draft",
        "created_by": 3,
        "updated_by": 3,
        "created_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_create_campaign_persists_draft_status():
    from app.communications import campaign_repository
    from app.communications.campaign_schemas import CampaignCreate

    db = FakeDb([Result(one=campaign_row())])

    created = campaign_repository.create_campaign(
        db=db,
        payload=CampaignCreate(
            name="Kilimani agency outreach",
            description="Draft campaign",
            campaign_type="cold",
            sender_identity_id=11,
        ),
        user=CurrentUser(
            id=3,
            name="Admin User",
            email="admin@example.test",
            role="admin",
        ),
    )

    sql, params = db.calls[0]

    assert created["status"] == "draft"
    assert "'draft'" in sql
    assert params["name"] == "Kilimani agency outreach"
    assert db.commits == 1


def test_recipient_duplicate_check_uses_lead_and_email():
    from app.communications import campaign_repository

    db = FakeDb([Result(one=SimpleNamespace(exists=True))])

    exists = campaign_repository.recipient_exists(
        db=db,
        campaign_id=21,
        lead_id=4,
        email_address=" Duplicate@Example.Test ",
    )

    sql, params = db.calls[0]

    assert exists is True
    assert "campaign_id = :campaign_id" in sql
    assert "lead_id = :lead_id" in sql
    assert "LOWER(recipient_email)" in sql
    assert params["email_address"] == "duplicate@example.test"


def test_enrolment_persists_snapshot_without_send_state():
    from app.communications import campaign_repository

    db = FakeDb([Result(one=SimpleNamespace(id=501))])

    recipient_id = campaign_repository.enroll_recipient(
        db=db,
        campaign_id=21,
        lead_id=1,
        recipient_email="alice@example.test",
        recipient_name="Alice",
        enrolled_by=3,
    )

    sql, params = db.calls[0]

    assert recipient_id == 501
    assert "INSERT INTO campaign_recipients" in sql
    assert "'enrolled'" in sql
    assert params["recipient_email"] == "alice@example.test"
    assert "email_messages" not in sql
