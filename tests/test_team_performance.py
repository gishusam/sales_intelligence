from types import SimpleNamespace


class Result:
    def __init__(self, rows=None):
        self.rows = rows or []

    def fetchall(self):
        return self.rows


def test_team_performance_tracks_monthly_my_leads_funnel():
    from app.routers.leads import get_team_performance

    class FakeDb:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params=None):
            sql = str(statement)
            self.calls.append((sql, params or {}))

            return Result(rows=[
                SimpleNamespace(
                    name="Samuel Ngugi",
                    new_leads=5,
                    called=4,
                    demos=2,
                    won=1,
                    calls=4,
                ),
                SimpleNamespace(
                    name="Jane Doe",
                    new_leads=2,
                    called=1,
                    demos=1,
                    won=0,
                    calls=1,
                ),
                SimpleNamespace(
                    name="Deployment Admin",
                    new_leads=0,
                    called=0,
                    demos=0,
                    won=0,
                    calls=0,
                ),
            ])

    db = FakeDb()

    response = get_team_performance(db=db)

    sql = "\n".join(call[0] for call in db.calls)

    # Calendar-month cohort, not rolling 30 days.
    assert "date_trunc('month', CURRENT_DATE)" in sql
    assert "INTERVAL '1 month'" in sql

    # A rep's monthly cohort starts when a lead is assigned into My Leads.
    assert "event_type = 'assigned'" in sql
    assert "lead_id" in sql
    assert "to_value" in sql
    assert "MIN(e.created_at) AS assigned_at" in sql

    # Funnel progression comes from status changes on that monthly cohort.
    # Events before the lead entered that rep's My Leads must not count.
    assert "s.created_at >= ma.assigned_at" in sql
    assert "status_change" in sql
    assert "monthly_assignments" in sql
    assert "COUNT(DISTINCT" in sql

    # Only real active sales-team users are shown.
    assert "users" in sql
    assert "is_active = TRUE" in sql
    assert "role IN ('sales', 'manager', 'admin')" in sql

    assert response == [
        {
            "name": "Samuel Ngugi",
            "new_leads": 5,
            "called": 4,
            "demos": 2,
            "won": 1,
            "conversion": 20.0,
        },
        {
            "name": "Jane Doe",
            "new_leads": 2,
            "called": 1,
            "demos": 1,
            "won": 0,
            "conversion": 0.0,
        },
    ]


def test_status_update_attributes_activity_to_authenticated_user():
    from app.routers.leads import StatusUpdate, update_status

    class FakeResult:
        def __init__(self, one=None):
            self.one = one

        def fetchone(self):
            return self.one

    class FakeDb:
        def __init__(self):
            self.calls = []
            self.commits = 0

        def execute(self, statement, params=None):
            sql = str(statement)
            params = params or {}
            self.calls.append((sql, params))

            if "SELECT status, assigned_to" in sql:
                return FakeResult(
                    SimpleNamespace(
                        status="new",
                        assigned_to="Samuel Ngugi",
                    )
                )

            if "UPDATE leads SET" in sql:
                return FakeResult(
                    SimpleNamespace(
                        id=1,
                        name="Sunrise Apartments",
                        status="called",
                        assigned_to="Samuel Ngugi",
                    )
                )

            return FakeResult()

        def commit(self):
            self.commits += 1

    db = FakeDb()
    user = SimpleNamespace(id=7, name="Samuel Ngugi")

    update_status(
        lead_id=1,
        body=StatusUpdate(
            status="called",
            changed_by="Someone Else",
        ),
        db=db,
        user=user,
    )

    event_call = next(
        (params for sql, params in db.calls if "INSERT INTO lead_events" in sql),
        None,
    )

    assert event_call is not None
    assert event_call["changed_by"] == "Samuel Ngugi"


def test_status_update_fetches_returning_row_before_commit():
    from app.routers.leads import StatusUpdate, update_status

    operations = []

    class SelectResult:
        def fetchone(self):
            return SimpleNamespace(
                status="new",
                assigned_to="Samuel Ngugi",
            )

    class UpdateResult:
        def fetchone(self):
            operations.append("fetch_returning_row")
            return SimpleNamespace(
                id=259,
                name="Test Lead",
                status="called",
                assigned_to="Samuel Ngugi",
            )

    class FakeDb:
        def execute(self, statement, params=None):
            sql = str(statement)

            if "SELECT status, assigned_to FROM leads" in sql:
                return SelectResult()

            if "UPDATE leads SET" in sql:
                return UpdateResult()

            return Result(rows=[])

        def commit(self):
            operations.append("commit")

    db = FakeDb()
    user = SimpleNamespace(id=7, name="Samuel Ngugi")

    response = update_status(
        lead_id=259,
        body=StatusUpdate(status="called"),
        db=db,
        user=user,
    )

    assert response["status"] == "called"

    assert operations.index("fetch_returning_row") < operations.index("commit")
