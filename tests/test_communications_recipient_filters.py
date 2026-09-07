import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

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


class RecordingDB:
    def __init__(self):
        self.last_params = None
        self.execute_calls = 0

    def execute(self, statement, params=None):
        self.execute_calls += 1
        sql = str(statement)

        if "INSERT INTO campaigns" in sql:
            self.last_params = params
            return _Result(
                row=SimpleNamespace(id=1, name=params["name"])
            )

        if "SELECT id, name, email FROM leads" in sql:
            return _Result(rows=[])

        raise AssertionError(f"Unexpected SQL in test: {sql}")

    def commit(self):
        pass


def _campaign_body(recipient_filter):
    return communications.CampaignCreate(
        name="Kilimani Event",
        subject="Upcoming Nyumba Zetu Event",
        body="Hello {contact_name}",
        communication_type="cold_outreach",
        recipient_type="leads",
        recipient_filter=recipient_filter,
    )


def test_campaign_filter_is_stored_as_valid_json():
    db = RecordingDB()
    user = SimpleNamespace(name="Samwel")
    expected = {"lead_type": "agency", "area": "Kilimani"}

    communications.create_campaign(
        _campaign_body(expected),
        db=db,
        user=user,
    )

    stored = db.last_params["recipient_filter"]
    try:
        decoded = json.loads(stored)
    except (TypeError, json.JSONDecodeError):
        decoded = None

    assert decoded == expected


def test_unsupported_audience_filter_is_rejected_before_database_write():
    db = RecordingDB()
    user = SimpleNamespace(name="Samwel")

    with pytest.raises(HTTPException) as exc_info:
        communications.create_campaign(
            _campaign_body({"area": "Kilimani", "unknown_filter": "oops"}),
            db=db,
            user=user,
        )

    assert exc_info.value.status_code == 400
    assert "unsupported" in str(exc_info.value.detail).lower()
    assert db.execute_calls == 0


def test_malformed_saved_filter_fails_closed_instead_of_selecting_all_leads():
    db = RecordingDB()
    campaign = SimpleNamespace(
        recipient_type="leads",
        recipient_filter="{'lead_type': 'agency', 'area': 'Kilimani'}",
        mailing_list_id=None,
        id=1,
    )

    with pytest.raises(HTTPException) as exc_info:
        communications._resolve_recipients(campaign, db)

    assert exc_info.value.status_code == 400
    assert "filter" in str(exc_info.value.detail).lower()
    assert db.execute_calls == 0
