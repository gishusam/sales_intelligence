import sys
import ast
import inspect
import textwrap
from pathlib import Path


SCRAPER_ROOT = Path(__file__).resolve().parents[1] / "scraper"
sys.path.insert(0, str(SCRAPER_ROOT))

from spiders import apartments
from spiders import database
from spiders import developers
from spiders import googlemaps


def test_apartment_run_does_not_retain_a_database_connection():
    tree = ast.parse(textwrap.dedent(inspect.getsource(apartments.run)))
    connection_references = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "conn"
    ]

    assert connection_references == []


def test_save_buildings_opens_and_closes_connection_at_persistence_time(
    monkeypatch,
):
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Connection:
        def cursor(self):
            calls.append("cursor")
            return Cursor()

        def commit(self):
            calls.append("commit")

        def close(self):
            calls.append("close")

    def connect(**kwargs):
        calls.append(("connect", kwargs))
        return Connection()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(apartments.psycopg2, "connect", connect)
    monkeypatch.setattr(
        apartments,
        "execute_values",
        lambda _cursor, sql, rows: calls.append(
            ("execute_values", len(rows), sql)
        ),
    )

    saved = apartments.save_buildings([{"building_name": "Test Towers"}])

    assert saved == 1
    assert calls[:2] == [
        ("connect", apartments.DB_CONFIG),
        "cursor",
    ]
    assert calls[2][0:2] == ("execute_values", 1)
    assert "scraped_at         = EXCLUDED.scraped_at" in calls[2][2]
    assert calls[3:] == ["commit", "close"]


def test_run_transaction_retries_transient_disconnect_with_fresh_connection(
    monkeypatch,
):
    calls = []

    class Connection:
        def __init__(self, attempt):
            self.attempt = attempt

        def commit(self):
            calls.append(("commit", self.attempt))

        def rollback(self):
            calls.append(("rollback", self.attempt))

        def close(self):
            calls.append(("close", self.attempt))

    def connect(**_kwargs):
        attempt = len([call for call in calls if call[0] == "connect"]) + 1
        calls.append(("connect", attempt))
        return Connection(attempt)

    def operation(conn):
        calls.append(("operation", conn.attempt))
        if conn.attempt == 1:
            raise database.psycopg2.OperationalError("connection closed")
        return "saved"

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(database.psycopg2, "connect", connect)
    monkeypatch.setattr(database.time, "sleep", lambda delay: calls.append(("sleep", delay)))

    result = database.run_transaction(
        operation,
        fallback_config={"host": "pooler.example.com"},
        operation_name="save test rows",
        base_delay_seconds=0.25,
    )

    assert result == "saved"
    assert calls == [
        ("connect", 1),
        ("operation", 1),
        ("rollback", 1),
        ("close", 1),
        ("sleep", 0.25),
        ("connect", 2),
        ("operation", 2),
        ("commit", 2),
        ("close", 2),
    ]


def test_agency_save_uses_operation_scoped_transaction(monkeypatch):
    calls = []

    class Connection:
        def cursor(self):
            return Cursor()

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def run_transaction(operation, **kwargs):
        calls.append(kwargs)
        return operation(Connection())

    monkeypatch.setattr(googlemaps, "run_transaction", run_transaction)
    monkeypatch.setattr(
        googlemaps,
        "execute_values",
        lambda _cursor, _sql, rows: calls.append(("rows", len(rows))),
    )

    saved = googlemaps.save_results([{
        "business_name": "Example Agency",
        "area": "Kilimani",
        "address": None,
        "phone": None,
        "website": None,
        "rating": None,
        "review_count": None,
        "category": None,
        "search_query": "property managers Kilimani",
        "maps_url": None,
    }])

    assert saved == 1
    assert calls[0]["operation_name"] == "Saving agency staging rows"
    assert calls[1] == ("rows", 1)


def test_developer_save_uses_operation_scoped_transaction(monkeypatch):
    calls = []

    class Connection:
        def cursor(self):
            return Cursor()

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _sql, params):
            calls.append(("execute", params[-1]))

    def run_transaction(operation, **kwargs):
        calls.append(kwargs)
        return operation(Connection())

    monkeypatch.setattr(developers, "run_transaction", run_transaction)

    developers.save_enrichment(27, {"status": "done"})

    assert calls[0]["operation_name"] == "Saving developer enrichment 27"
    assert calls[1] == ("execute", 27)
