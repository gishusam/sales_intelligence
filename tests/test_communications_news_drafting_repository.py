import os
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.test")


class Result:
    def __init__(self, *, one=None, rows=None):
        self._one = one
        self._rows = rows or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class FakeDb:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return self.responses.pop(0)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def source_row():
    return SimpleNamespace(
        id=51,
        name="Property News Kenya",
        source_type="rss",
        url="https://news.example.test/feed",
        publisher="Property News Kenya",
        trust_tier="review_required",
        is_active=False,
        created_by=3,
        updated_by=3,
        created_at=None,
        updated_at=None,
    )


def test_create_source_persists_inactive_default():
    from app.auth import CurrentUser
    from app.communications import news_drafting_repository
    from app.communications.news_drafting_schemas import NewsSourceCreate

    db = FakeDb([Result(one=source_row())])

    created = news_drafting_repository.create_source(
        db=db,
        payload=NewsSourceCreate(
            name="Property News Kenya",
            source_type="rss",
            url="https://news.example.test/feed",
            publisher="Property News Kenya",
        ),
        user=CurrentUser(
            id=3,
            name="Admin",
            email="admin@example.test",
            role="admin",
        ),
    )

    sql, params = db.calls[0]

    assert created["is_active"] is False
    assert "INSERT INTO newsletter_sources" in sql
    assert params["is_active"] is False
    assert db.commits == 1


def test_article_insert_uses_dedupe_identity():
    from app.communications import news_drafting_repository

    db = FakeDb([Result(one=SimpleNamespace(id=61))])

    article_id = news_drafting_repository.create_article(
        db=db,
        source_id=51,
        external_id="external-61",
        title="Property story",
        canonical_url="https://news.example.test/story",
        publisher="Publisher",
        published_at=datetime.now(timezone.utc),
        summary="Summary",
        content_text="Content",
        fingerprint="fingerprint-61",
        metadata={"county": "Nairobi"},
    )

    sql, params = db.calls[0]

    assert article_id == 61
    assert "INSERT INTO newsletter_articles" in sql
    assert params["fingerprint"] == "fingerprint-61"
    assert params["canonical_url"] == (
        "https://news.example.test/story"
    )


def test_generated_newsletter_is_forced_to_draft():
    from app.communications import news_drafting_repository

    db = FakeDb([Result(one=SimpleNamespace(id=71))])

    newsletter_id = news_drafting_repository.create_generated_newsletter(
        db=db,
        name="August brief",
        subject="August intelligence",
        preview_text="Latest developments",
        sender_identity_id=11,
        blocks=[],
        body_text="Unsubscribe: {unsubscribe_link}",
        body_html='<a href="{unsubscribe_link}">Unsubscribe</a>',
        generation_run_id=801,
        source_type="ai_assisted",
        status="draft",
        created_by=3,
        updated_by=3,
        approved_by=None,
        scheduled_for=None,
    )

    sql, params = db.calls[0]

    assert newsletter_id == 71
    assert "INSERT INTO newsletter_drafts" in sql
    assert "'draft'" in sql
    assert params["source_type"] == "ai_assisted"
    assert "approved_by" not in params
