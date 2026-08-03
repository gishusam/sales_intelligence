import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.test")

ROOT = Path(__file__).resolve().parents[1]


def test_newsletter_unsubscribe_route_cannot_be_shadowed():
    source = (
        ROOT
        / "backend"
        / "app"
        / "routers"
        / "communication_newsletters.py"
    ).read_text(encoding="utf-8")

    assert '@router.get("/{newsletter_id:int}"' in source


def test_migration_has_one_atomic_commit_and_canonical_constraints():
    source = (
        ROOT
        / "migrations"
        / "2026_08_communications.sql"
    ).read_text(encoding="utf-8").lower()

    assert source.count("commit;") == 1
    assert source.rstrip().endswith("commit;")

    for token in {
        "message_type in",
        "'campaign'",
        "'automation'",
        "'newsletter_test'",
        "'processing'",
        "'dead_letter'",
        "'delivered'",
        "'bounced'",
        "'complained'",
        "'unsubscribed'",
        "'ready'",
        "'enrolled'",
        "'in_review'",
        "'sending'",
        "'api'",
        "alter column email_message_id drop not null",
        "alter column title drop not null",
        "alter column from_email drop not null",
        "alter column email drop not null",
    }:
        assert token in source


def test_migration_protects_all_later_communications_tables():
    source = (
        ROOT
        / "migrations"
        / "2026_08_communications.sql"
    ).read_text(encoding="utf-8").lower()

    for table in {
        "newsletter_recipients",
        "lead_communication_state",
        "newsletter_articles",
        "newsletter_ingestion_runs",
        "newsletter_generation_runs",
        "newsletter_draft_articles",
    }:
        assert f"alter table {table} enable row level security" in source


def test_smtp_fails_closed_in_staging_without_credentials(monkeypatch):
    from app.communications.delivery import OutboundMessage
    from app.communications.smtp_provider import SMTPEmailProvider

    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("COMMUNICATIONS_SMTP_MOCK", "false")

    for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(key, raising=False)

    result = SMTPEmailProvider().send(
        OutboundMessage(
            from_name="Nyumba Zetu",
            from_email="sales@example.test",
            reply_to=None,
            to_email="reviewer@example.test",
            subject="Test",
            body_text="Hello",
        )
    )

    assert result.accepted is False
    assert "not configured" in (result.error_message or "").lower()


def test_smtp_mock_is_explicitly_available_in_development(monkeypatch):
    from app.communications.delivery import OutboundMessage
    from app.communications.smtp_provider import SMTPEmailProvider

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("COMMUNICATIONS_SMTP_MOCK", "true")

    for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(key, raising=False)

    result = SMTPEmailProvider().send(
        OutboundMessage(
            from_name="Nyumba Zetu",
            from_email="sales@example.test",
            reply_to=None,
            to_email="reviewer@example.test",
            subject="Test",
            body_text="Hello",
        )
    )

    assert result.accepted is True
    assert result.provider_message_id == "mock"


def test_staging_readiness_requires_smtp_credentials():
    from app.communications.communications_readiness import (
        environment_issues,
    )

    issues = environment_issues(
        {
            "ENVIRONMENT": "staging",
            "COMMUNICATIONS_WORKER_TOKEN": "worker",
            "EMAIL_WEBHOOK_SECRET": "webhook",
            "NEWSLETTER_UNSUBSCRIBE_SECRET": "unsubscribe",
            "PUBLIC_API_BASE_URL": "https://staging.example.test",
            "COMMUNICATIONS_SMTP_MOCK": "false",
        }
    )

    assert "SMTP_HOST is required" in issues
    assert "SMTP_USER is required" in issues
    assert "SMTP_PASSWORD is required" in issues


def test_runtime_response_enums_cover_lifecycle_states():
    from app.communications.campaign_schemas import (
        CampaignRecipientResponse,
        CampaignResponse,
    )
    from app.communications.newsletter_schemas import NewsletterResponse

    campaign = CampaignResponse(
        id=1,
        name="Campaign",
        description=None,
        campaign_type="cold",
        sender_identity_id=1,
        status="ready",
        created_by=1,
        updated_by=1,
        created_at=None,
        updated_at=None,
    )
    recipient = CampaignRecipientResponse(
        id=1,
        campaign_id=1,
        lead_id=1,
        recipient_email="lead@example.test",
        recipient_name="Lead",
        status="delivered",
        suppression_reason=None,
        enrolled_by=1,
        enrolled_at=None,
        created_at=None,
        updated_at=None,
    )
    newsletter = NewsletterResponse(
        id=1,
        name="Newsletter",
        subject="Subject",
        preview_text=None,
        status="sending",
        sender_identity_id=1,
        blocks=[],
        body_text="Body",
        body_html="<p>Body</p>",
        created_by=1,
        updated_by=1,
        reviewed_by=1,
        approved_by=1,
        reviewed_at=None,
        approved_at=None,
        created_at=None,
        updated_at=None,
    )

    assert campaign.status == "ready"
    assert recipient.status == "delivered"
    assert newsletter.status == "sending"


@pytest.mark.parametrize(
    "constructor, kwargs",
    [
        (
            "newsletter",
            {
                "kind": "link",
                "text": "Unsafe",
                "url": "javascript:alert(1)",
            },
        ),
        (
            "source",
            {
                "name": "Unsafe",
                "source_type": "rss",
                "url": "javascript:alert(1)",
                "publisher": "Publisher",
            },
        ),
    ],
)
def test_unsafe_url_schemes_are_rejected(constructor, kwargs):
    if constructor == "newsletter":
        from app.communications.newsletter_schemas import NewsletterBlock

        target = NewsletterBlock
    else:
        from app.communications.news_drafting_schemas import NewsSourceCreate

        target = NewsSourceCreate

    with pytest.raises(ValidationError, match=r"HTTP or HTTPS"):
        target(**kwargs)


def test_newsletter_test_send_failure_is_audited(monkeypatch):
    from app.auth import CurrentUser
    from app.communications import newsletter_repository as repository
    from app.communications.delivery import ProviderResult
    from app.communications.newsletter_schemas import NewsletterTestSendRequest
    from app.communications.newsletter_service import (
        NewsletterValidationError,
        test_send,
    )

    events = []

    monkeypatch.setenv(
        "NEWSLETTER_UNSUBSCRIBE_SECRET",
        "newsletter-secret",
    )
    monkeypatch.setattr(
        repository,
        "get_newsletter",
        lambda **kwargs: {
            "id": 71,
            "subject": "Test subject",
            "sender_identity_id": 11,
            "body_text": "Unsubscribe: {unsubscribe_link}",
            "body_html": '<a href="{unsubscribe_link}">Unsubscribe</a>',
        },
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: {
            "id": 11,
            "display_name": "Nyumba Zetu",
            "email_address": "sales@example.test",
            "reply_to_address": None,
            "provider": "smtp",
            "is_active": True,
        },
    )
    monkeypatch.setattr(
        repository,
        "create_test_message",
        lambda **kwargs: 901,
    )
    monkeypatch.setattr(
        repository,
        "mark_test_failed",
        lambda **kwargs: events.append(kwargs),
    )
    monkeypatch.setattr(
        repository,
        "mark_test_sent",
        lambda **kwargs: pytest.fail(
            "Rejected delivery must not be marked sent"
        ),
    )

    class RejectingProvider:
        def send(self, message):
            return ProviderResult(
                accepted=False,
                error_message="Provider rejected test",
            )

    db = SimpleNamespace(
        commit=lambda: None,
        rollback=lambda: None,
    )

    with pytest.raises(
        NewsletterValidationError,
        match=r"Provider rejected test",
    ):
        test_send(
            db=db,
            newsletter_id=71,
            payload=NewsletterTestSendRequest(
                to_email="reviewer@example.test",
            ),
            user=CurrentUser(
                id=3,
                name="Admin",
                email="admin@example.test",
                role="admin",
            ),
            provider=RejectingProvider(),
        )

    assert events[0]["message_id"] == 901
    assert events[0]["error_message"] == "Provider rejected test"


def test_newsletter_test_messages_receive_unique_keys():
    from app.communications import newsletter_repository as repository

    class Result:
        def __init__(self, identifier):
            self.identifier = identifier

        def fetchone(self):
            return SimpleNamespace(id=self.identifier)

    class FakeDb:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            self.calls.append(params)
            return Result(len(self.calls))

    db = FakeDb()
    common = {
        "db": db,
        "newsletter_id": 71,
        "sender_identity_id": 11,
        "sent_by": 3,
        "recipient_email": "reviewer@example.test",
        "subject": "Subject",
        "body_text": "Body",
        "body_html": "<p>Body</p>",
    }

    repository.create_test_message(**common)
    repository.create_test_message(**common)

    assert (
        db.calls[0]["idempotency_key"]
        != db.calls[1]["idempotency_key"]
    )
