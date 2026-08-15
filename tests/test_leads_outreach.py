import os
from datetime import date, datetime, timezone
from types import SimpleNamespace


os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.com")


class Result:
    def __init__(self, *, one=None, rows=None, scalar_value=None):
        self._one = one
        self._rows = rows or []
        self._scalar_value = scalar_value

    def scalar(self):
        return self._scalar_value

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


def test_outreach_list_returns_email_status_and_pagination():
    from app.routers import leads

    count_row = SimpleNamespace(
        total=4,
        emailed=3,
        not_emailed=1,
    )

    lead_row = SimpleNamespace(
        id=17,
        name="Example Agency",
        owner_name="Owner",
        phone="+254700000000",
        email="hello@example.test",
        website="https://example.test",
        area="Kilimani",
        lead_type="agency",
        score=82.5,
        status="new",
        assigned_to=None,
        ai_score="LOW_HANGING_FRUIT",
        last_contacted=None,
        email_sent_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        follow_up_date=date(2026, 8, 3),
        contact_attempts=1,
        last_email_sent_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        last_email_type="cold",
        last_email_by="Deployment Admin",
    )

    class FakeDb:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), params))

            # First query: total matching leads
            if len(self.calls) == 1:
                return Result(scalar_value=3)

            # Second query: paginated lead rows
            if len(self.calls) == 2:
                return Result(rows=[lead_row])

            # Third query: summary counts for tabs
            return Result(one=count_row)

    db = FakeDb()

    response = leads.get_outreach_leads(
        lead_type="agency",
        filter_by="emailed",
        area=None,
        page=1,
        limit=20,
        db=db,
    )

    assert response["counts"] == {
        "all": 4,
        "emailed": 3,
        "not_emailed": 1,
    }

    assert response["total"] == 3
    assert response["page"] == 1
    assert response["pages"] == 1

    assert response["data"][0]["email_status"] == "emailed"
    assert response["data"][0]["last_email_type"] == "cold"
    assert (
        response["data"][0]["last_email_sent_at"]
        == "2026-07-26T00:00:00+00:00"
    )
    assert response["data"][0]["last_email_by"] == "Deployment Admin"

    # Total query should apply the selected outreach filter.
    assert "email_sent_at IS NOT NULL" in db.calls[0][0]
    assert db.calls[0][1] == {
        "lead_type": "agency",
    }

    # Lead query should paginate correctly.
    assert "LIMIT :limit OFFSET :offset" in db.calls[1][0]
    assert db.calls[1][1] == {
        "lead_type": "agency",
        "limit": 20,
        "offset": 0,
    }


def test_by_area_filters_on_lead_type_when_supplied():
    from app.routers import leads

    class FakeDb:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), params))
            return Result(
                rows=[
                    SimpleNamespace(
                        area="Kilimani",
                        count=12,
                    )
                ]
            )

    db = FakeDb()

    response = leads.get_by_area(
        lead_type="agency",
        db=db,
    )

    assert response == [
        {
            "area": "Kilimani",
            "count": 12,
        }
    ]
    assert "lead_type = :lead_type" in db.calls[0][0]
    assert db.calls[0][1] == {
        "lead_type": "agency",
    }


def test_by_area_ignores_malformed_legacy_filter():
    from app.routers import leads

    class FakeDb:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), params))
            return Result(
                rows=[
                    SimpleNamespace(
                        area="Kilimani",
                        count=12,
                    )
                ]
            )

    db = FakeDb()

    response = leads.get_by_area(
        lead_type="[object Object]",
        db=db,
    )

    assert response == [
        {
            "area": "Kilimani",
            "count": 12,
        }
    ]
    assert "lead_type = :lead_type" not in db.calls[0][0]
    assert db.calls[0][1] == {}