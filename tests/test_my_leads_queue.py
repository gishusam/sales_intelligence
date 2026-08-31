from types import SimpleNamespace


class Result:
    def __init__(self, *, scalar_value=None, one=None, rows=None):
        self.scalar_value = scalar_value
        self.one = one
        self.rows = rows or []

    def scalar(self):
        return self.scalar_value

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


def test_my_leads_not_contacted_queue_filters_before_pagination():
    from app.routers.leads import get_my_leads

    class FakeDb:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            sql = str(statement)
            self.calls.append((sql, params))

            # Total for the selected queue.
            if "AS needs_attention" in sql:
                return Result(one=SimpleNamespace(
                    needs_attention=0,
                    new=0,
                    not_contacted=0,
                    follow_up=0,
                    contacted=0,
                    demo_booked=0,
                ))

            if "SELECT COUNT(*)" in sql and "LIMIT" not in sql:
                return Result(scalar_value=4)

            return Result(rows=[])

    db = FakeDb()
    user = SimpleNamespace(id=7, name="Sam")

    response = get_my_leads(
        queue="not_contacted",
        q=None,
        status=None,
        page=1,
        limit=20,
        db=db,
        user=user,
    )

    sql = "\n".join(call[0] for call in db.calls)

    assert "last_contacted IS NULL" in sql
    assert "LIMIT :limit OFFSET :offset" in sql
    assert response["total"] == 4


def test_my_leads_search_filters_before_pagination():
    from app.routers.leads import get_my_leads

    class FakeDb:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            sql = str(statement)
            self.calls.append((sql, params))

            if "AS needs_attention" in sql:
                return Result(one=SimpleNamespace(
                    needs_attention=0,
                    new=0,
                    not_contacted=0,
                    follow_up=0,
                    contacted=0,
                    demo_booked=0,
                ))

            if "SELECT COUNT(*)" in sql and "LIMIT" not in sql:
                return Result(scalar_value=2)

            return Result(rows=[])

    db = FakeDb()
    user = SimpleNamespace(id=7, name="Sam")

    response = get_my_leads(
        queue="all",
        q="sunrise",
        status=None,
        page=1,
        limit=20,
        db=db,
        user=user,
    )

    sql = "\n".join(call[0] for call in db.calls)

    assert "ILIKE :q" in sql
    assert db.calls[0][1]["q"] == "%sunrise%"
    assert "LIMIT :limit OFFSET :offset" in sql
    assert response["total"] == 2


def test_my_leads_status_filters_before_pagination():
    from app.routers.leads import get_my_leads

    class FakeDb:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            sql = str(statement)
            self.calls.append((sql, params))

            if "AS needs_attention" in sql:
                return Result(one=SimpleNamespace(
                    needs_attention=0,
                    new=0,
                    not_contacted=0,
                    follow_up=0,
                    contacted=0,
                    demo_booked=0,
                ))

            if "SELECT COUNT(*)" in sql and "LIMIT" not in sql:
                return Result(scalar_value=3)

            return Result(rows=[])

    db = FakeDb()
    user = SimpleNamespace(id=7, name="Sam")

    response = get_my_leads(
        queue="all",
        q=None,
        status="demo_booked",
        page=1,
        limit=20,
        db=db,
        user=user,
    )

    sql = "\n".join(call[0] for call in db.calls)

    assert "status = :status" in sql
    assert db.calls[0][1]["status"] == "demo_booked"
    assert "LIMIT :limit OFFSET :offset" in sql
    assert response["total"] == 3


def test_my_leads_follow_up_queue_only_returns_due_or_overdue_leads():
    from app.routers.leads import get_my_leads

    class FakeDb:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            sql = str(statement)
            self.calls.append((sql, params))

            if "AS needs_attention" in sql:
                return Result(one=SimpleNamespace(
                    needs_attention=0,
                    new=0,
                    not_contacted=0,
                    follow_up=0,
                    contacted=0,
                    demo_booked=0,
                ))

            if "SELECT COUNT(*)" in sql and "LIMIT" not in sql:
                return Result(scalar_value=5)

            return Result(rows=[])

    db = FakeDb()
    user = SimpleNamespace(id=7, name="Sam")

    response = get_my_leads(
        queue="follow_up",
        q=None,
        status=None,
        page=1,
        limit=20,
        db=db,
        user=user,
    )

    sql = "\n".join(call[0] for call in db.calls)

    assert "follow_up_date IS NOT NULL" in sql
    assert "follow_up_date <= CURRENT_DATE" in sql
    assert "LIMIT :limit OFFSET :offset" in sql
    assert response["total"] == 5


def test_my_leads_simple_queues_filter_before_pagination():
    from app.routers.leads import get_my_leads

    cases = [
        ("new", "status = 'new'"),
        ("contacted", "last_contacted IS NOT NULL"),
        ("demo_booked", "status = 'demo_booked'"),
    ]

    for queue, expected_sql in cases:
        class FakeDb:
            def __init__(self):
                self.calls = []

            def execute(self, statement, params):
                sql = str(statement)
                self.calls.append((sql, params))

                if "AS needs_attention" in sql:
                    return Result(one=SimpleNamespace(
                        needs_attention=0,
                        new=0,
                        not_contacted=0,
                        follow_up=0,
                        contacted=0,
                        demo_booked=0,
                    ))

                if "SELECT COUNT(*)" in sql and "LIMIT" not in sql:
                    return Result(scalar_value=2)

                return Result(rows=[])

        db = FakeDb()
        user = SimpleNamespace(id=7, name="Sam")

        response = get_my_leads(
            queue=queue,
            q=None,
            status=None,
            page=1,
            limit=20,
            db=db,
            user=user,
        )

        sql = "\n".join(call[0] for call in db.calls)

        assert expected_sql in sql
        assert "LIMIT :limit OFFSET :offset" in sql
        assert response["total"] == 2


def test_my_leads_needs_attention_filters_and_prioritizes_actionable_leads():
    from app.routers.leads import get_my_leads

    class FakeDb:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            sql = str(statement)
            self.calls.append((sql, params))

            if "AS needs_attention" in sql:
                return Result(one=SimpleNamespace(
                    needs_attention=0,
                    new=0,
                    not_contacted=0,
                    follow_up=0,
                    contacted=0,
                    demo_booked=0,
                ))

            if "SELECT COUNT(*)" in sql and "LIMIT" not in sql:
                return Result(scalar_value=6)

            return Result(rows=[])

    db = FakeDb()
    user = SimpleNamespace(id=7, name="Sam")

    response = get_my_leads(
        queue="needs_attention",
        q=None,
        status=None,
        page=1,
        limit=20,
        db=db,
        user=user,
    )

    sql = "\n".join(call[0] for call in db.calls)

    # Needs Attention only includes actionable leads.
    assert "follow_up_date <= CURRENT_DATE" in sql
    assert "last_contacted IS NULL" in sql
    assert "status = 'new'" in sql

    # Priority: due follow-up -> never contacted -> new -> higher score.
    assert "CASE" in sql
    assert "WHEN follow_up_date IS NOT NULL" in sql
    assert "WHEN last_contacted IS NULL" in sql
    assert "WHEN status = 'new'" in sql
    assert "score DESC NULLS LAST" in sql

    assert response["total"] == 6


def test_my_leads_returns_counts_for_quick_queues():
    from app.routers.leads import get_my_leads

    class FakeDb:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            sql = str(statement)
            self.calls.append((sql, params))

            if "AS needs_attention" in sql:
                return Result(one=SimpleNamespace(
                    needs_attention=6,
                    new=3,
                    not_contacted=4,
                    follow_up=2,
                    contacted=8,
                    demo_booked=1,
                ))

            if "SELECT COUNT(*)" in sql and "LIMIT" not in sql:
                return Result(scalar_value=6)

            return Result(rows=[])

    db = FakeDb()
    user = SimpleNamespace(id=7, name="Sam")

    response = get_my_leads(
        queue="needs_attention",
        q=None,
        status=None,
        page=1,
        limit=20,
        db=db,
        user=user,
    )

    assert response["counts"] == {
        "needs_attention": 6,
        "new": 3,
        "not_contacted": 4,
        "follow_up": 2,
        "contacted": 8,
        "demo_booked": 1,
    }

    sql = "\n".join(call[0] for call in db.calls)

    assert "COUNT(*) FILTER" in sql
    assert "AS needs_attention" in sql
    assert "AS not_contacted" in sql
    assert "AS follow_up" in sql
    assert "AS contacted" in sql
    assert "AS demo_booked" in sql


def test_my_leads_queues_use_the_expected_work_order():
    from app.routers.leads import get_my_leads

    expected_orders = {
        "new": "created_at DESC",
        "contacted": "last_contacted DESC NULLS LAST",
        "follow_up": "follow_up_date ASC NULLS LAST",
    }

    for queue, expected_order in expected_orders.items():
        class FakeDb:
            def __init__(self):
                self.calls = []

            def execute(self, statement, params):
                sql = str(statement)
                self.calls.append((sql, params))

                if "AS needs_attention" in sql:
                    return Result(one=SimpleNamespace(
                        needs_attention=0,
                        new=0,
                        not_contacted=0,
                        follow_up=0,
                        contacted=0,
                        demo_booked=0,
                    ))

                if "SELECT COUNT(*)" in sql and "LIMIT" not in sql:
                    return Result(scalar_value=0)

                return Result(rows=[])

        db = FakeDb()
        user = SimpleNamespace(id=7, name="Sam")

        get_my_leads(
            queue=queue,
            q=None,
            status=None,
            page=1,
            limit=20,
            db=db,
            user=user,
        )

        data_sql = next(
            sql for sql, _ in db.calls
            if "LIMIT :limit OFFSET :offset" in sql
        )

        assert expected_order in data_sql
