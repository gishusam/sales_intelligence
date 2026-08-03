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
    def __init__(self, *, one=None):
        self._one = one

    def fetchone(self):
        return self._one


class FakeDb:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.commits = 0

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return self.responses.pop(0)

    def commit(self):
        self.commits += 1


def test_create_newsletter_persists_rendered_content():
    from app.auth import CurrentUser
    from app.communications import newsletter_repository

    row = SimpleNamespace(
        id=71,
        name="August brief",
        subject="August brief",
        preview_text=None,
        status="draft",
        sender_identity_id=11,
        blocks=[],
        body_text="Unsubscribe: {unsubscribe_link}",
        body_html='<a href="{unsubscribe_link}">Unsubscribe</a>',
        created_by=3,
        updated_by=3,
        reviewed_by=None,
        approved_by=None,
        reviewed_at=None,
        approved_at=None,
        created_at=None,
        updated_at=None,
    )
    db = FakeDb([Result(one=row)])

    created = newsletter_repository.create_newsletter(
        db=db,
        values={
            "name": "August brief",
            "subject": "August brief",
            "preview_text": None,
            "sender_identity_id": 11,
            "blocks": [],
            "body_text": "Unsubscribe: {unsubscribe_link}",
            "body_html": '<a href="{unsubscribe_link}">Unsubscribe</a>',
        },
        user=CurrentUser(
            id=3,
            name="Admin",
            email="admin@example.test",
            role="admin",
        ),
    )

    sql, params = db.calls[0]

    assert created["status"] == "draft"
    assert "INSERT INTO newsletter_drafts" in sql
    assert "'draft'" in sql
    assert params["body_text"] == "Unsubscribe: {unsubscribe_link}"
    assert db.commits == 1


def test_recipient_snapshot_normalizes_email():
    from app.communications import newsletter_repository

    db = FakeDb([Result(one=SimpleNamespace(id=801))])

    recipient_id = newsletter_repository.create_recipient(
        db=db,
        newsletter_id=71,
        lead_id=1,
        recipient_email=" Alice@Example.Test ",
        recipient_name="Alice",
        company_name="Alpha Properties",
        added_by=3,
    )

    sql, params = db.calls[0]

    assert recipient_id == 801
    assert "INSERT INTO newsletter_recipients" in sql
    assert params["recipient_email"] == "alice@example.test"
