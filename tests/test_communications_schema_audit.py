import os
from types import SimpleNamespace

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.test")


class Result:
    def __init__(self, *, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class FakeDb:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return self.responses.pop(0)


def test_schema_audit_reports_missing_objects():
    from app.communications.communications_schema_audit import (
        inspect_schema,
    )

    db = FakeDb(
        [
            Result(
                rows=[
                    SimpleNamespace(table_name="email_messages"),
                ]
            ),
            Result(
                rows=[
                    SimpleNamespace(
                        table_name="email_messages",
                        column_name="status",
                    ),
                ]
            ),
            Result(rows=[]),
        ]
    )

    result = inspect_schema(db=db)

    assert result["ready"] is False
    assert "email_events" in result["missing_tables"]
    assert "email_messages.provider_message_id" in (
        result["missing_columns"]
    )
    assert "uq_email_events_provider_event" in (
        result["missing_indexes"]
    )


def test_schema_audit_accepts_complete_snapshot(monkeypatch):
    from app.communications import communications_schema_audit as audit

    table_rows = [
        SimpleNamespace(table_name=name)
        for name in audit.REQUIRED_TABLES
    ]
    column_rows = [
        SimpleNamespace(
            table_name=table_name,
            column_name=column_name,
        )
        for table_name, column_name in audit.REQUIRED_COLUMNS
    ]
    index_rows = [
        SimpleNamespace(indexname=name)
        for name in audit.REQUIRED_INDEXES
    ]

    result = audit.inspect_schema(
        db=FakeDb(
            [
                Result(rows=table_rows),
                Result(rows=column_rows),
                Result(rows=index_rows),
            ]
        )
    )

    assert result == {
        "ready": True,
        "missing_tables": [],
        "missing_columns": [],
        "missing_indexes": [],
    }


def test_audit_queries_only_metadata_catalogues():
    from app.communications.communications_schema_audit import (
        inspect_schema,
    )

    db = FakeDb(
        [
            Result(rows=[]),
            Result(rows=[]),
            Result(rows=[]),
        ]
    )

    inspect_schema(db=db)

    statements = "\n".join(call[0] for call in db.calls).lower()

    assert "information_schema.tables" in statements
    assert "information_schema.columns" in statements
    assert "pg_indexes" in statements
    assert "insert " not in statements
    assert "update " not in statements
    assert "delete " not in statements
