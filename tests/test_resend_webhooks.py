import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

from app.routers.resend_webhooks import process_resend_event


class _Result:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class RecordingDB:
    def __init__(self, duplicate=False):
        self.duplicate = duplicate
        self.updated_sql = None
        self.updated_params = None
        self.committed = False

    def execute(self, statement, params=None):
        sql = str(statement)

        if "INSERT INTO resend_webhook_events" in sql:
            if self.duplicate:
                return _Result(row=None)

            return _Result(
                row=SimpleNamespace(id=1)
            )

        if "UPDATE campaign_recipients" in sql:
            self.updated_sql = sql
            self.updated_params = params
            return _Result(rowcount=1)

        raise AssertionError(
            f"Unexpected SQL in test: {sql}"
        )

    def commit(self):
        self.committed = True


def test_delivered_event_updates_matching_recipient():
    db = RecordingDB()

    payload = {
        "type": "email.delivered",
        "created_at": "2026-08-13T08:00:00Z",
        "data": {
            "email_id": "resend-email-123",
        },
    }

    result = process_resend_event(
        db=db,
        svix_id="msg-delivered-1",
        payload=payload,
    )

    assert result["status"] == "processed"
    assert result["event_type"] == "email.delivered"
    assert result["resend_id"] == "resend-email-123"

    assert "delivered_at" in db.updated_sql
    assert db.updated_params["resend_id"] == "resend-email-123"
    assert db.committed is True


def test_opened_event_increments_open_count():
    db = RecordingDB()

    payload = {
        "type": "email.opened",
        "created_at": "2026-08-13T08:01:00Z",
        "data": {
            "email_id": "resend-email-456",
        },
    }

    result = process_resend_event(
        db=db,
        svix_id="msg-opened-1",
        payload=payload,
    )

    assert result["status"] == "processed"

    assert "opened_at" in db.updated_sql
    assert "open_count = open_count + 1" in db.updated_sql
    assert db.updated_params["resend_id"] == "resend-email-456"
    assert db.committed is True


def test_bounced_event_records_reason_and_status():
    db = RecordingDB()

    payload = {
        "type": "email.bounced",
        "created_at": "2026-08-13T08:02:00Z",
        "data": {
            "email_id": "resend-email-789",
            "bounce": {
                "message": "Mailbox does not exist",
                "type": "Permanent",
            },
        },
    }

    result = process_resend_event(
        db=db,
        svix_id="msg-bounced-1",
        payload=payload,
    )

    assert result["status"] == "processed"

    assert "bounced_at" in db.updated_sql
    assert db.updated_params["resend_id"] == "resend-email-789"
    assert db.updated_params["reason"] == "Mailbox does not exist"
    assert db.committed is True


def test_duplicate_webhook_does_not_update_recipient_again():
    db = RecordingDB(duplicate=True)

    payload = {
        "type": "email.opened",
        "created_at": "2026-08-13T08:03:00Z",
        "data": {
            "email_id": "resend-email-456",
        },
    }

    result = process_resend_event(
        db=db,
        svix_id="msg-already-processed",
        payload=payload,
    )

    assert result["status"] == "duplicate"
    assert db.updated_sql is None
    assert db.updated_params is None
