import os
import sys
from datetime import datetime, timezone
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
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class DetailDB:
    def execute(self, statement, params=None):
        sql = str(statement)

        if "SELECT * FROM campaigns" in sql:
            return Result(
                row=SimpleNamespace(
                    id=7,
                    name="Kilimani Demo",
                    subject="Nyumba Zetu Demo",
                    body="Hello",
                    sender_name="Nyumba Zetu",
                    sender_email="sales@nyumbazetu.com",
                    reply_to=None,
                    status="sent",
                    recipient_type="leads",
                    communication_type="cold_outreach",
                    total_recipients=10,
                    sent_count=10,
                    failed_count=1,
                    created_by="Samwel",
                    created_at=datetime(
                        2026, 8, 13, 8, 0,
                        tzinfo=timezone.utc,
                    ),
                    finished_at=datetime(
                        2026, 8, 13, 8, 5,
                        tzinfo=timezone.utc,
                    ),
                )
            )

        if "GROUP BY status" in sql:
            return Result(
                rows=[
                    SimpleNamespace(
                        status="delivered",
                        count=8,
                    )
                ]
            )

        raise AssertionError(f"Unexpected SQL: {sql}")


def test_campaign_detail_includes_communication_type():
    result = communications.get_campaign(
        campaign_id=7,
        db=DetailDB(),
        user=SimpleNamespace(name="Samwel"),
    )

    assert result["id"] == 7
    assert result["communication_type"] == "cold_outreach"
    assert result["recipient_type"] == "leads"
