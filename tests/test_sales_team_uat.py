import os
from types import SimpleNamespace


os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.com")


class Result:
    def __init__(self, *, one=None, rows=None):
        self.one = one
        self.rows = rows or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


def test_not_qualified_is_a_valid_lead_status():
    from app.routers.leads import VALID_STATUSES

    assert "not_qualified" in VALID_STATUSES


def test_bulk_assignment_uses_a_real_active_rep_and_records_events():
    from app.auth import CurrentUser
    from app.routers.leads import BulkAssignment, assign_leads_bulk

    class FakeDb:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), params))
            if len(self.calls) == 1:
                return Result(one=SimpleNamespace(id=7, name="Aisha Hassan"))
            if len(self.calls) == 2:
                return Result(rows=[SimpleNamespace(id=11), SimpleNamespace(id=12)])
            return Result()

        def commit(self):
            self.committed = True

    db = FakeDb()
    user = CurrentUser(id=3, email="sam@example.test", name="Sam", role="sales")
    response = assign_leads_bulk(
        BulkAssignment(lead_ids=[12, 11, 12], assignee_id=7), db=db, user=user
    )

    assert response == {"assigned_to": "Aisha Hassan", "updated": 2, "missing_ids": []}
    assert db.calls[1][1] == {"assigned_to": "Aisha Hassan", "lead_ids": [11, 12]}
    assert "CAST(:lead_ids AS integer[])" in db.calls[2][0]
    assert "INSERT INTO lead_events" in db.calls[2][0]
    assert db.committed is True


def test_pipeline_apartment_identity_does_not_include_area():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "pipeline.py").read_text(encoding="utf-8")
    apartment_sql = source.split("# 5c: Apartment staging leads", 1)[1].split("# 5d:", 1)[0]

    assert "AND LOWER(l.area) = LOWER(a.search_area)" not in apartment_sql
    assert "l.lead_type = 'apartment'" in apartment_sql
    assert "COALESCE(l.phone, c.contact_phone)" in apartment_sql
    assert "GROUP BY LOWER(TRIM(REGEXP_REPLACE(a.building_name" in apartment_sql
    assert "REGEXP_REPLACE(l.name" in apartment_sql
