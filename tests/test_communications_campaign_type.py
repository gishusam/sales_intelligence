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


class Result:
    def fetchone(self):
        return SimpleNamespace(
            id=42,
            name="August Product Update",
        )


class RecordingDB:
    def __init__(self):
        self.sql = None
        self.params = None
        self.committed = False

    def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = params
        return Result()

    def commit(self):
        self.committed = True


def test_create_campaign_persists_communication_type():
    db = RecordingDB()

    body = communications.CampaignCreate(
        name="August Product Update",
        subject="What's new at Nyumba Zetu",
        body="Hello",
        communication_type="newsletter",
        recipient_type="leads",
    )

    result = communications.create_campaign(
        body=body,
        db=db,
        user=SimpleNamespace(name="Samwel"),
    )

    assert result == {
        "id": 42,
        "name": "August Product Update",
        "status": "draft",
    }

    assert "communication_type" in db.sql
    assert db.params["communication_type"] == "newsletter"
    assert db.committed is True
