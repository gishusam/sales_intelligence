import os
from datetime import datetime, timedelta, timezone
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
    "NEWSLETTER_UNSUBSCRIBE_SECRET",
    "newsletter-secret",
)

from app.auth import CurrentUser
from app.communications.delivery import ProviderResult


def user(role="admin"):
    return CurrentUser(
        id=3,
        name="Admin User",
        email="admin@example.test",
        role=role,
    )


def newsletter(**overrides):
    value = {
        "id": 71,
        "name": "August property brief",
        "subject": "August property brief",
        "status": "approved",
        "sender_identity_id": 11,
        "body_text": (
            "Hello {contact_name},\n"
            "Latest property news.\n"
            "Unsubscribe: {unsubscribe_link}"
        ),
        "body_html": (
            "<p>Hello {contact_name},</p>"
            "<p>Latest property news.</p>"
            '<p><a href="{unsubscribe_link}">Unsubscribe</a></p>'
        ),
        "created_by": 3,
        "scheduled_for": None,
    }
    value.update(overrides)
    return value


def sender(**overrides):
    value = {
        "id": 11,
        "display_name": "Nyumba Zetu Sales",
        "email_address": "sales@nyumbazetu.example",
        "reply_to_address": "reply@nyumbazetu.example",
        "provider": "smtp",
        "is_active": True,
    }
    value.update(overrides)
    return value


def recipients():
    return [
        {
            "id": 801,
            "newsletter_id": 71,
            "lead_id": 1,
            "recipient_email": "alice@example.test",
            "recipient_name": "Alice",
            "company_name_snapshot": "Alpha Properties",
            "status": "eligible",
        },
        {
            "id": 802,
            "newsletter_id": 71,
            "lead_id": 2,
            "recipient_email": "blocked@example.test",
            "recipient_name": "Blocked",
            "company_name_snapshot": "Blocked Properties",
            "status": "eligible",
        },
    ]


def queued_message(**overrides):
    value = {
        "id": 901,
        "newsletter_id": 71,
        "newsletter_recipient_id": 801,
        "campaign_id": None,
        "campaign_recipient_id": None,
        "campaign_step_id": None,
        "lead_id": 1,
        "template_id": None,
        "sender_identity_id": 11,
        "sent_by": 3,
        "recipient_email": "alice@example.test",
        "recipient_name": "Alice",
        "subject": "August property brief",
        "body_text": (
            "Hello Alice,\nLatest property news.\n"
            "Unsubscribe: https://example.test/unsubscribe/token"
        ),
        "body_html": (
            "<p>Hello Alice,</p><p>Latest property news.</p>"
            '<p><a href="https://example.test/unsubscribe/token">'
            "Unsubscribe</a></p>"
        ),
        "message_type": "newsletter",
        "status": "processing",
        "idempotency_key": "newsletter:71:recipient:801",
        "attempt_count": 1,
        "scheduled_for": datetime.now(timezone.utc),
    }
    value.update(overrides)
    return value


class FakeProvider:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or ProviderResult(
            accepted=True,
            provider_message_id="provider-newsletter-1",
        )

    def send(self, message):
        self.calls.append(message)
        return self.result


def test_schedule_request_requires_timezone():
    from app.communications.newsletter_delivery_schemas import (
        NewsletterScheduleRequest,
    )

    with pytest.raises(
        ValidationError,
        match=r"timezone-aware",
    ):
        NewsletterScheduleRequest(
            scheduled_for=datetime(2026, 8, 5, 9, 0),
        )


def test_only_approved_newsletter_can_be_scheduled(monkeypatch):
    from app.communications import newsletter_delivery_repository as repository
    from app.communications.newsletter_delivery_schemas import (
        NewsletterScheduleRequest,
    )
    from app.communications.newsletter_delivery_service import (
        NewsletterDeliveryStateError,
        schedule_newsletter,
    )

    monkeypatch.setattr(
        repository,
        "get_newsletter",
        lambda **kwargs: newsletter(status="draft"),
    )

    with pytest.raises(
        NewsletterDeliveryStateError,
        match=r"Only approved newsletters can be scheduled",
    ):
        schedule_newsletter(
            db=object(),
            newsletter_id=71,
            payload=NewsletterScheduleRequest(
                scheduled_for=datetime.now(timezone.utc),
            ),
            user=user(),
        )


def test_schedule_queues_personalized_messages_and_suppresses(
    monkeypatch,
):
    from app.communications import newsletter_delivery_repository as repository
    from app.communications.newsletter_delivery_schemas import (
        NewsletterScheduleRequest,
    )
    from app.communications.newsletter_delivery_service import (
        schedule_newsletter,
    )

    queued = []
    events = []

    monkeypatch.setattr(
        repository,
        "get_newsletter",
        lambda **kwargs: newsletter(),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "list_eligible_recipients",
        lambda **kwargs: recipients(),
    )
    monkeypatch.setattr(
        repository,
        "is_suppressed",
        lambda **kwargs: (
            kwargs["email_address"] == "blocked@example.test"
        ),
    )
    monkeypatch.setattr(
        repository,
        "mark_recipient_suppressed",
        lambda **kwargs: events.append(("suppressed", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "queue_newsletter_message",
        lambda **kwargs: queued.append(kwargs) or True,
    )
    monkeypatch.setattr(
        repository,
        "mark_recipient_queued",
        lambda **kwargs: events.append(("queued", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "mark_newsletter_scheduled",
        lambda **kwargs: events.append(("scheduled", kwargs)),
    )

    db = SimpleNamespace(
        commit=lambda: events.append(("commit", None)),
        rollback=lambda: events.append(("rollback", None)),
    )
    scheduled_for = datetime.now(timezone.utc) + timedelta(hours=1)

    result = schedule_newsletter(
        db=db,
        newsletter_id=71,
        payload=NewsletterScheduleRequest(
            scheduled_for=scheduled_for,
        ),
        user=user(),
    )

    assert result["status"] == "scheduled"
    assert result["queued_messages"] == 1
    assert result["suppressed_recipients"] == 1
    assert len(queued) == 1

    job = queued[0]
    assert job["idempotency_key"] == "newsletter:71:recipient:801"
    assert job["scheduled_for"] == scheduled_for
    assert "Hello Alice" in job["body_text"]
    assert "{unsubscribe_link}" not in job["body_text"]
    assert "/unsubscribe/" in job["body_text"]
    scheduled_event = next(
        payload
        for event_name, payload in events
        if event_name == "scheduled"
    )
    assert scheduled_event["newsletter_id"] == 71
    assert scheduled_event["scheduled_for"] == scheduled_for
    assert scheduled_event["updated_by"] == 3
    assert scheduled_event["db"] is db


def test_schedule_is_idempotent_and_never_sends(monkeypatch):
    from app.communications import newsletter_delivery_repository as repository
    from app.communications.newsletter_delivery_schemas import (
        NewsletterScheduleRequest,
    )
    from app.communications.newsletter_delivery_service import (
        schedule_newsletter,
    )
    from app.communications.smtp_provider import SMTPEmailProvider

    monkeypatch.setattr(
        repository,
        "get_newsletter",
        lambda **kwargs: newsletter(),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "list_eligible_recipients",
        lambda **kwargs: [recipients()[0]],
    )
    monkeypatch.setattr(
        repository,
        "is_suppressed",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        repository,
        "queue_newsletter_message",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        repository,
        "mark_recipient_queued",
        lambda **kwargs: pytest.fail(
            "Existing queue row must not re-mark recipient"
        ),
    )
    monkeypatch.setattr(
        repository,
        "mark_newsletter_scheduled",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        SMTPEmailProvider,
        "send",
        lambda *args, **kwargs: pytest.fail(
            "Scheduling must never send email"
        ),
    )

    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    result = schedule_newsletter(
        db=db,
        newsletter_id=71,
        payload=NewsletterScheduleRequest(
            scheduled_for=datetime.now(timezone.utc),
        ),
        user=user(),
    )

    assert result["queued_messages"] == 0
    assert result["existing_messages"] == 1


def test_newsletter_worker_sends_exact_persisted_content(monkeypatch):
    from app.communications import newsletter_delivery_repository as repository
    from app.communications.newsletter_delivery_worker import (
        process_newsletter_message,
    )

    events = []

    monkeypatch.setattr(
        repository,
        "get_newsletter",
        lambda **kwargs: newsletter(status="scheduled"),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "is_suppressed",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        repository,
        "mark_message_sent",
        lambda **kwargs: events.append(("message_sent", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "mark_recipient_sent",
        lambda **kwargs: events.append(("recipient_sent", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "mark_newsletter_sending",
        lambda **kwargs: events.append(("sending", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "finalize_newsletter_if_complete",
        lambda **kwargs: events.append(("finalize", kwargs)),
    )

    provider = FakeProvider()
    db = SimpleNamespace(
        commit=lambda: events.append(("commit", None)),
        rollback=lambda: None,
    )

    result = process_newsletter_message(
        db=db,
        message=queued_message(),
        provider=provider,
        max_attempts=5,
        now=datetime.now(timezone.utc),
    )

    assert result == "sent"
    assert len(provider.calls) == 1
    outbound = provider.calls[0]
    assert outbound.subject == "August property brief"
    assert outbound.body_text == queued_message()["body_text"]
    assert outbound.body_html == queued_message()["body_html"]
    assert any(event[0] == "recipient_sent" for event in events)


def test_newsletter_worker_rechecks_suppression(monkeypatch):
    from app.communications import newsletter_delivery_repository as repository
    from app.communications.newsletter_delivery_worker import (
        process_newsletter_message,
    )

    events = []

    monkeypatch.setattr(
        repository,
        "get_newsletter",
        lambda **kwargs: newsletter(status="scheduled"),
    )
    monkeypatch.setattr(
        repository,
        "is_suppressed",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        repository,
        "mark_message_suppressed",
        lambda **kwargs: events.append(("message", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "mark_recipient_suppressed",
        lambda **kwargs: events.append(("recipient", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "finalize_newsletter_if_complete",
        lambda **kwargs: events.append(("finalize", kwargs)),
    )

    provider = FakeProvider()
    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    result = process_newsletter_message(
        db=db,
        message=queued_message(),
        provider=provider,
        max_attempts=5,
        now=datetime.now(timezone.utc),
    )

    assert result == "suppressed"
    assert provider.calls == []
    assert [event[0] for event in events[:2]] == [
        "message",
        "recipient",
    ]


def test_newsletter_worker_retries_then_dead_letters(monkeypatch):
    from app.communications import newsletter_delivery_repository as repository
    from app.communications.newsletter_delivery_worker import (
        process_newsletter_message,
    )

    events = []

    monkeypatch.setattr(
        repository,
        "get_newsletter",
        lambda **kwargs: newsletter(status="scheduled"),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "is_suppressed",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        repository,
        "mark_message_retry",
        lambda **kwargs: events.append(("retry", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "mark_message_dead_letter",
        lambda **kwargs: events.append(("dead", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "mark_recipient_failed",
        lambda **kwargs: events.append(("recipient_failed", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "finalize_newsletter_if_complete",
        lambda **kwargs: events.append(("finalize", kwargs)),
    )

    provider = FakeProvider(
        ProviderResult(
            accepted=False,
            error_message="Provider unavailable",
        )
    )
    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
    now = datetime.now(timezone.utc)

    assert process_newsletter_message(
        db=db,
        message=queued_message(attempt_count=2),
        provider=provider,
        max_attempts=5,
        now=now,
    ) == "retry"
    assert events[0][0] == "retry"

    events.clear()

    assert process_newsletter_message(
        db=db,
        message=queued_message(attempt_count=5),
        provider=provider,
        max_attempts=5,
        now=now,
    ) == "dead_letter"
    assert events[0][0] == "dead"
    assert any(event[0] == "recipient_failed" for event in events)


def test_cancel_newsletter_cancels_pending_jobs(monkeypatch):
    from app.communications import newsletter_delivery_repository as repository
    from app.communications.newsletter_delivery_service import (
        cancel_newsletter,
    )

    events = []

    monkeypatch.setattr(
        repository,
        "get_newsletter",
        lambda **kwargs: newsletter(status="scheduled"),
    )
    monkeypatch.setattr(
        repository,
        "cancel_newsletter_and_jobs",
        lambda **kwargs: events.append(kwargs),
    )

    db = SimpleNamespace(commit=lambda: events.append("commit"))

    result = cancel_newsletter(
        db=db,
        newsletter_id=71,
        user=user(),
    )

    assert result == {
        "newsletter_id": 71,
        "status": "cancelled",
    }
    assert events[0]["newsletter_id"] == 71


def test_sales_cannot_schedule_newsletter():
    from app.communications.newsletter_delivery_schemas import (
        NewsletterScheduleRequest,
    )
    from app.routers import communication_newsletter_delivery

    with pytest.raises(HTTPException) as error:
        communication_newsletter_delivery.schedule_newsletter(
            newsletter_id=71,
            payload=NewsletterScheduleRequest(
                scheduled_for=datetime.now(timezone.utc),
            ),
            db=object(),
            user=user("sales"),
        )

    assert error.value.status_code == 403


def test_main_mounts_newsletter_delivery_router():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert (
        "from app.routers import communication_newsletter_delivery "
        "as communication_newsletter_delivery_router"
    ) in source
    assert (
        "app.include_router("
        "communication_newsletter_delivery_router.router"
        ")"
    ) in source
