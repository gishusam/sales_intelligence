import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.test")
os.environ.setdefault(
    "COMMUNICATIONS_WORKER_TOKEN",
    "test-worker-token",
)

from app.auth import CurrentUser


def user(role="admin"):
    return CurrentUser(
        id=3,
        name="Admin User",
        email="admin@example.test",
        role=role,
    )


def source(**overrides):
    value = {
        "id": 51,
        "name": "Property News Kenya",
        "source_type": "rss",
        "url": "https://news.example.test/feed",
        "publisher": "Property News Kenya",
        "trust_tier": "verified",
        "is_active": True,
        "created_by": 3,
        "updated_by": 3,
        "created_at": None,
        "updated_at": None,
    }
    value.update(overrides)
    return value


def article(article_id=61, **overrides):
    value = {
        "id": article_id,
        "source_id": 51,
        "external_id": f"external-{article_id}",
        "title": f"Property story {article_id}",
        "canonical_url": (
            f"https://news.example.test/articles/{article_id}"
        ),
        "publisher": "Property News Kenya",
        "published_at": datetime(
            2026,
            8,
            3,
            9,
            0,
            tzinfo=timezone.utc,
        ),
        "summary": "A concise property market update.",
        "content_text": "Detailed source text.",
        "fingerprint": f"fingerprint-{article_id}",
        "status": "new",
        "metadata": {"county": "Nairobi"},
        "created_at": None,
        "updated_at": None,
    }
    value.update(overrides)
    return value


def sender(**overrides):
    value = {
        "id": 11,
        "display_name": "Nyumba Zetu Sales",
        "email_address": "sales@nyumbazetu.example",
        "reply_to_address": None,
        "provider": "smtp",
        "is_active": True,
    }
    value.update(overrides)
    return value


class FakeGenerator:
    model_name = "fake-newsletter-generator"

    def __init__(self):
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return {
            "subject": "Nairobi property intelligence",
            "preview_text": "Three developments worth watching",
            "blocks": [
                {
                    "kind": "heading",
                    "text": "Nairobi property intelligence",
                },
                {
                    "kind": "paragraph",
                    "text": "A reviewed summary of current developments.",
                },
                {
                    "kind": "link",
                    "text": "Read Property story 61",
                    "url": "https://news.example.test/articles/61",
                },
            ],
        }


def test_source_defaults_to_inactive():
    from app.communications.news_drafting_schemas import NewsSourceCreate

    created = NewsSourceCreate(
        name="Property News Kenya",
        source_type="rss",
        url="https://news.example.test/feed",
        publisher="Property News Kenya",
    )

    assert created.is_active is False
    assert created.trust_tier == "review_required"


def test_source_update_requires_a_change():
    from app.communications.news_drafting_schemas import NewsSourceUpdate

    with pytest.raises(
        ValidationError,
        match=r"At least one source field",
    ):
        NewsSourceUpdate()


def test_ingestion_requires_timezone_aware_published_at():
    from app.communications.news_drafting_schemas import NewsArticleInput

    with pytest.raises(
        ValidationError,
        match=r"timezone-aware",
    ):
        NewsArticleInput(
            title="Property update",
            canonical_url="https://news.example.test/story",
            publisher="Publisher",
            published_at=datetime(2026, 8, 3, 9, 0),
        )


def test_article_fingerprint_is_stable():
    from app.communications.news_drafting_service import (
        article_fingerprint,
    )

    first = article_fingerprint(
        title="  Nairobi RENT update ",
        canonical_url="https://NEWS.example.test/story/",
        publisher="Publisher",
        published_at=datetime(
            2026,
            8,
            3,
            9,
            0,
            tzinfo=timezone.utc,
        ),
    )
    second = article_fingerprint(
        title="nairobi rent update",
        canonical_url="https://news.example.test/story",
        publisher="publisher",
        published_at=datetime(
            2026,
            8,
            3,
            9,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert first == second


def test_inactive_source_cannot_ingest(monkeypatch):
    from app.communications import news_drafting_repository as repository
    from app.communications.news_drafting_schemas import NewsIngestionRequest
    from app.communications.news_drafting_service import (
        NewsDraftingValidationError,
        ingest_articles,
    )

    monkeypatch.setattr(
        repository,
        "get_source",
        lambda **kwargs: source(is_active=False),
    )

    with pytest.raises(
        NewsDraftingValidationError,
        match=r"inactive",
    ):
        ingest_articles(
            db=object(),
            payload=NewsIngestionRequest(
                source_id=51,
                articles=[
                    {
                        "title": "Property update",
                        "canonical_url": (
                            "https://news.example.test/story"
                        ),
                        "publisher": "Publisher",
                        "published_at": datetime.now(timezone.utc),
                    }
                ],
            ),
        )


def test_ingestion_deduplicates_existing_articles(monkeypatch):
    from app.communications import news_drafting_repository as repository
    from app.communications.news_drafting_schemas import NewsIngestionRequest
    from app.communications.news_drafting_service import ingest_articles

    created = []

    monkeypatch.setattr(
        repository,
        "get_source",
        lambda **kwargs: source(),
    )
    monkeypatch.setattr(
        repository,
        "find_article_by_identity",
        lambda **kwargs: (
            {"id": 61}
            if kwargs["canonical_url"].endswith("/existing")
            else None
        ),
    )
    monkeypatch.setattr(
        repository,
        "create_article",
        lambda **kwargs: created.append(kwargs) or 62,
    )
    monkeypatch.setattr(
        repository,
        "record_ingestion_run",
        lambda **kwargs: 701,
    )

    db = SimpleNamespace(
        commit=lambda: None,
        rollback=lambda: None,
    )

    result = ingest_articles(
        db=db,
        payload=NewsIngestionRequest(
            source_id=51,
            articles=[
                {
                    "title": "Existing story",
                    "canonical_url": (
                        "https://news.example.test/existing"
                    ),
                    "publisher": "Publisher",
                    "published_at": datetime.now(timezone.utc),
                },
                {
                    "title": "New story",
                    "canonical_url": "https://news.example.test/new",
                    "publisher": "Publisher",
                    "published_at": datetime.now(timezone.utc),
                },
            ],
        ),
    )

    assert result["received"] == 2
    assert result["created"] == 1
    assert result["duplicates"] == 1
    assert created[0]["title"] == "New story"


def test_draft_generation_requires_active_sender(monkeypatch):
    from app.communications import news_drafting_repository as repository
    from app.communications.news_drafting_schemas import (
        NewsDraftGenerationRequest,
    )
    from app.communications.news_drafting_service import (
        NewsDraftingValidationError,
        generate_newsletter_draft,
    )

    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(is_active=False),
    )

    with pytest.raises(
        NewsDraftingValidationError,
        match=r"Sender identity",
    ):
        generate_newsletter_draft(
            db=object(),
            payload=NewsDraftGenerationRequest(
                article_ids=[61],
                sender_identity_id=11,
                newsletter_name="August intelligence brief",
            ),
            user=user(),
            generator=FakeGenerator(),
        )


def test_ai_generation_creates_reviewable_draft_with_provenance(
    monkeypatch,
):
    from app.communications import news_drafting_repository as repository
    from app.communications.news_drafting_schemas import (
        NewsDraftGenerationRequest,
    )
    from app.communications.news_drafting_service import (
        generate_newsletter_draft,
    )

    events = []

    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "get_articles_by_ids",
        lambda **kwargs: {
            61: article(61),
            62: article(62),
        },
    )
    monkeypatch.setattr(
        repository,
        "create_generation_run",
        lambda **kwargs: events.append(("run", kwargs)) or 801,
    )
    monkeypatch.setattr(
        repository,
        "create_generated_newsletter",
        lambda **kwargs: events.append(
            ("newsletter", kwargs)
        ) or 71,
    )
    monkeypatch.setattr(
        repository,
        "link_draft_articles",
        lambda **kwargs: events.append(("links", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "mark_articles_used",
        lambda **kwargs: events.append(("used", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "complete_generation_run",
        lambda **kwargs: events.append(("complete", kwargs)),
    )

    generator = FakeGenerator()
    db = SimpleNamespace(
        commit=lambda: events.append(("commit", None)),
        rollback=lambda: events.append(("rollback", None)),
    )

    result = generate_newsletter_draft(
        db=db,
        payload=NewsDraftGenerationRequest(
            article_ids=[61, 62],
            sender_identity_id=11,
            newsletter_name="August intelligence brief",
        ),
        user=user(),
        generator=generator,
    )

    assert result["newsletter_id"] == 71
    assert result["generation_run_id"] == 801
    assert result["status"] == "draft"
    assert len(generator.calls) == 1

    created = next(
        item
        for kind, item in events
        if kind == "newsletter"
    )
    assert created["status"] == "draft"
    assert created["source_type"] == "ai_assisted"
    assert created["generation_run_id"] == 801
    assert created["approved_by"] is None
    assert created["scheduled_for"] is None
    assert "{unsubscribe_link}" in created["body_text"]
    assert "https://news.example.test/articles/61" in created["body_text"]

    linked = next(
        item
        for kind, item in events
        if kind == "links"
    )
    assert linked["article_ids"] == [61, 62]


def test_generation_rejects_missing_articles(monkeypatch):
    from app.communications import news_drafting_repository as repository
    from app.communications.news_drafting_schemas import (
        NewsDraftGenerationRequest,
    )
    from app.communications.news_drafting_service import (
        NewsDraftingValidationError,
        generate_newsletter_draft,
    )

    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "get_articles_by_ids",
        lambda **kwargs: {61: article(61)},
    )

    with pytest.raises(
        NewsDraftingValidationError,
        match=r"Missing article IDs: 62",
    ):
        generate_newsletter_draft(
            db=object(),
            payload=NewsDraftGenerationRequest(
                article_ids=[61, 62],
                sender_identity_id=11,
                newsletter_name="August intelligence brief",
            ),
            user=user(),
            generator=FakeGenerator(),
        )


def test_generation_never_calls_smtp(monkeypatch):
    from app.communications import news_drafting_repository as repository
    from app.communications.news_drafting_schemas import (
        NewsDraftGenerationRequest,
    )
    from app.communications.news_drafting_service import (
        generate_newsletter_draft,
    )
    from app.communications.smtp_provider import SMTPEmailProvider

    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "get_articles_by_ids",
        lambda **kwargs: {61: article(61)},
    )
    monkeypatch.setattr(
        repository,
        "create_generation_run",
        lambda **kwargs: 801,
    )
    monkeypatch.setattr(
        repository,
        "create_generated_newsletter",
        lambda **kwargs: 71,
    )
    monkeypatch.setattr(
        repository,
        "link_draft_articles",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "mark_articles_used",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "complete_generation_run",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        SMTPEmailProvider,
        "send",
        lambda *args, **kwargs: pytest.fail(
            "Draft generation must never send email"
        ),
    )

    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    result = generate_newsletter_draft(
        db=db,
        payload=NewsDraftGenerationRequest(
            article_ids=[61],
            sender_identity_id=11,
            newsletter_name="August intelligence brief",
        ),
        user=user(),
        generator=FakeGenerator(),
    )

    assert result["status"] == "draft"


def test_default_ai_generator_fails_closed_without_api_key(
    monkeypatch,
):
    from app.communications.news_drafting_generator import (
        NewsDraftGeneratorConfigurationError,
        build_default_generator,
    )

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(
        NewsDraftGeneratorConfigurationError,
        match=r"GEMINI_API_KEY",
    ):
        build_default_generator()


def test_sales_can_read_articles_but_cannot_create_source(
    monkeypatch,
):
    from app.communications.news_drafting_schemas import NewsSourceCreate
    from app.routers import communication_news_drafting

    monkeypatch.setattr(
        communication_news_drafting.news_drafting_repository,
        "list_articles",
        lambda **kwargs: [article()],
    )

    rows = communication_news_drafting.list_news_articles(
        source_id=None,
        status_filter=None,
        limit=100,
        db=object(),
        user=user("sales"),
    )

    assert rows[0]["id"] == 61

    with pytest.raises(HTTPException) as error:
        communication_news_drafting.create_news_source(
            payload=NewsSourceCreate(
                name="Property News Kenya",
                source_type="rss",
                url="https://news.example.test/feed",
                publisher="Property News Kenya",
            ),
            db=object(),
            user=user("sales"),
        )

    assert error.value.status_code == 403


def test_internal_ingestion_requires_worker_token():
    from app.routers import communication_news_drafting

    with pytest.raises(HTTPException) as error:
        communication_news_drafting.ingest_news_articles(
            payload=SimpleNamespace(),
            x_worker_token="wrong-token",
            db=object(),
        )

    assert error.value.status_code == 401


def test_main_mounts_news_drafting_router():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert (
        "from app.routers import communication_news_drafting "
        "as communication_news_drafting_router"
    ) in source
    assert (
        "app.include_router("
        "communication_news_drafting_router.router"
        ")"
    ) in source
