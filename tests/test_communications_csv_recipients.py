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
        self.inserted = []
        self.deleted = False
        self.committed = False

    def execute(self, statement, params=None):
        sql = str(statement)

        if "SELECT id, recipient_type, status" in sql:
            return _Result(
                SimpleNamespace(
                    id=7,
                    recipient_type="csv_upload",
                    status="draft",
                )
            )

        if "DELETE FROM campaign_recipients" in sql:
            self.deleted = True
            return _Result()

        if "INSERT INTO campaign_recipients" in sql:
            self.inserted.append(params)
            return _Result()

        raise AssertionError(f"Unexpected SQL: {sql}")

    def commit(self):
        self.committed = True


def test_csv_recipients_are_validated_deduplicated_and_saved_pending():
    db = RecordingDB()
    user = SimpleNamespace(name="Samwel")

    body = communications.CampaignRecipientUpload(
        recipients=[
            {
                "name": "Sam Test",
                "email": "sam@example.com",
            },
            {
                "name": "Jane Test",
                "email": "jane@example.com",
            },
            {
                "name": "Duplicate Sam",
                "email": "SAM@example.com",
            },
            {
                "name": "Bad",
                "email": "not-an-email",
            },
        ]
    )

    result = communications.upload_campaign_recipients(
        7,
        body,
        db=db,
        user=user,
    )

    assert result == {
        "uploaded": 4,
        "valid": 2,
        "invalid": 1,
        "duplicates": 1,
    }

    assert db.deleted is True
    assert db.committed is True

    assert len(db.inserted) == 2

    assert db.inserted[0] == {
        "campaign_id": 7,
        "email": "sam@example.com",
        "name": "Sam Test",
    }

    assert db.inserted[1] == {
        "campaign_id": 7,
        "email": "jane@example.com",
        "name": "Jane Test",
    }
