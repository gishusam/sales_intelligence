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


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class RecentCampaignDB:
    def execute(self, statement, params=None):
        return _Result(
            rows=[
                SimpleNamespace(
                    id=7,
                    name="Kilimani Demo",
                    subject="Nyumba Zetu Demo",
                    status="sent",
                    recipient_type="leads",
                    sender_email="sales@nyumbazetu.com",
                    total_recipients=10,
                    sent_count=10,
                    delivered_count=8,
                    opened_count=5,
                    clicked_count=2,
                    bounced_count=1,
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
            ]
        )


class PerformanceDB:
    def execute(self, statement, params=None):
        sql = str(statement)

        if "FROM campaigns" in sql:
            return _Result(
                row=SimpleNamespace(
                    id=7,
                    name="Kilimani Demo",
                    subject="Nyumba Zetu Demo",
                    status="sent",
                    recipient_type="leads",
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

        if "COUNT(*) AS recipients" in sql:
            return _Result(
                row=SimpleNamespace(
                    recipients=10,
                    sent=10,
                    delivered=8,
                    opened=5,
                    clicked=2,
                    bounced=1,
                    failed=1,
                    open_events=7,
                    click_events=3,
                )
            )

        if "FROM campaign_recipients" in sql:
            return _Result(
                rows=[
                    SimpleNamespace(
                        email="demo@example.com",
                        name="Demo Property",
                        status="delivered",
                        resend_id="resend-123",
                        sent_at=datetime(
                            2026, 8, 13, 8, 1,
                            tzinfo=timezone.utc,
                        ),
                        delivered_at=datetime(
                            2026, 8, 13, 8, 2,
                            tzinfo=timezone.utc,
                        ),
                        opened_at=datetime(
                            2026, 8, 13, 8, 3,
                            tzinfo=timezone.utc,
                        ),
                        clicked_at=None,
                        bounced_at=None,
                        failed_at=None,
                        open_count=2,
                        click_count=0,
                        bounce_reason=None,
                        error=None,
                        last_event_at=datetime(
                            2026, 8, 13, 8, 3,
                            tzinfo=timezone.utc,
                        ),
                    )
                ]
            )

        raise AssertionError(
            f"Unexpected SQL in test: {sql}"
        )


def test_recent_campaigns_include_delivery_metrics():
    result = communications.get_campaigns(
        db=RecentCampaignDB(),
        user=SimpleNamespace(name="Samwel"),
    )

    campaign = result[0]

    assert campaign["total_recipients"] == 10
    assert campaign["sent_count"] == 10
    assert campaign["delivered_count"] == 8
    assert campaign["opened_count"] == 5
    assert campaign["clicked_count"] == 2
    assert campaign["bounced_count"] == 1
    assert campaign["failed_count"] == 1


def test_campaign_performance_returns_summary_rates_and_recipients():
    result = communications.get_campaign_performance(
        campaign_id=7,
        db=PerformanceDB(),
        user=SimpleNamespace(name="Samwel"),
    )

    assert result["campaign"]["id"] == 7
    assert result["campaign"]["name"] == "Kilimani Demo"

    summary = result["summary"]

    assert summary["recipients"] == 10
    assert summary["sent"] == 10
    assert summary["delivered"] == 8
    assert summary["opened"] == 5
    assert summary["clicked"] == 2
    assert summary["bounced"] == 1
    assert summary["failed"] == 1

    assert summary["delivery_rate"] == 80.0
    assert summary["open_rate"] == 62.5
    assert summary["click_rate"] == 25.0

    assert summary["open_events"] == 7
    assert summary["click_events"] == 3

    assert len(result["recipients"]) == 1

    recipient = result["recipients"][0]

    assert recipient["email"] == "demo@example.com"
    assert recipient["resend_id"] == "resend-123"
    assert recipient["open_count"] == 2
