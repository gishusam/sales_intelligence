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

from app.routers import communications


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class RecordingDB:
    def __init__(self):
        self.updated_params = None
        self.committed = False

    def execute(self, statement, params=None):
        sql = str(statement)

        if "SELECT id, status" in sql and "FROM campaigns" in sql:
            return _Result(
                row=SimpleNamespace(
                    id=1,
                    status="reviewed",
                )
            )

        if "UPDATE campaigns" in sql:
            self.updated_params = params
            return _Result()

        raise AssertionError(f"Unexpected SQL in test: {sql}")

    def commit(self):
        self.committed = True


def test_reviewed_campaign_content_can_be_updated_without_changing_audience():
    db = RecordingDB()
    user = SimpleNamespace(name="Samwel")

    body = communications.CampaignUpdate(
        subject="CEO demo",
        body="Hello from Nyumba Zetu",
        sender_name="Nyumba Zetu",
        sender_email="onboarding@resend.dev",
    )

    result = communications.update_campaign(
        1,
        body,
        db=db,
        user=user,
    )

    assert result["id"] == 1
    assert result["status"] == "reviewed"

    assert db.updated_params["subject"] == "CEO demo"
    assert db.updated_params["body"] == "Hello from Nyumba Zetu"
    assert db.updated_params["sender_email"] == "onboarding@resend.dev"

    assert "recipient_filter" not in db.updated_params
    assert "recipient_type" not in db.updated_params

    assert db.committed is True