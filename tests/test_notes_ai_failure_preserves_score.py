import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.routers import notes


class FakeResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeLeadRow:
    _mapping = {
        "id": 1,
        "name": "Example Lead",
        "lead_type": "landlord",
        "area": "Nairobi",
        "score": 50,
        "phone": "0700000000",
        "website": None,
        "ai_score": "NURTURE",
    }


class FakeDb:
    def __init__(self):
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        self.calls.append((sql, params))

        if "SELECT id, name, lead_type" in sql:
            return FakeResult(FakeLeadRow())

        if "INSERT INTO lead_notes" in sql:
            return FakeResult(
                SimpleNamespace(
                    id=99,
                    created_at=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
                )
            )

        return FakeResult()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


async def failing_score(*args, **kwargs):
    raise ValueError("broken model response")


def test_ai_failure_does_not_create_fake_score_or_follow_up(monkeypatch):
    monkeypatch.setattr(notes, "score_with_llm", failing_score)

    db = FakeDb()
    user = SimpleNamespace(name="Samuel Ngugi")

    result = asyncio.run(
        notes.create_note(
            lead_id=1,
            body=notes.NoteCreate(note="Called customer, discussed the product"),
            db=db,
            user=user,
        )
    )

    insert_params = next(
        params
        for sql, params in db.calls
        if "INSERT INTO lead_notes" in sql
    )

    update_sql, update_params = next(
        (sql, params)
        for sql, params in db.calls
        if "UPDATE leads SET" in sql
    )

    assert insert_params["ai_score"] is None
    assert insert_params["follow_up_days"] is None

    assert update_params["ai_score"] is None
    assert update_params["follow_up_date"] is None

    assert "COALESCE(:ai_score, ai_score)" in update_sql
    assert "COALESCE(:follow_up_date, follow_up_date)" in update_sql

    assert result["ai_score"] is None
    assert result["follow_up_days"] is None
    assert result["follow_up_date"] is None
    assert "AI scoring unavailable" in result["ai_score_reason"]
