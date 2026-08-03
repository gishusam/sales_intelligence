import os
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

from app.auth import CurrentUser


class NoWriteDb:
    def commit(self):
        raise AssertionError("Preview must not commit")


class FakeProvider:
    def __init__(self, result=None):
        from app.communications.delivery import ProviderResult

        self.calls = []
        self.result = result or ProviderResult(
            accepted=True,
            provider_message_id="provider-123",
        )

    def send(self, message):
        self.calls.append(message)
        return self.result


def user():
    return CurrentUser(
        id=5,
        name="Samuel Ngugi",
        email="samuel@example.test",
        role="sales",
    )


def lead():
    return {
        "id": 41,
        "name": "Example Properties",
        "owner_name": "Alice",
        "contact_person": "Alice",
        "email": "alice@example.test",
        "area": "Kilimani",
    }


def template():
    return {
        "id": 9,
        "slug": "agency-introduction",
        "name": "Agency introduction",
        "template_type": "cold",
        "subject": "A better workflow for {company_name}",
        "body_text": (
            "Hi {contact_name},\n"
            "We support teams in {area}.\n"
            "Regards,\n{rep_name}\n{rep_email}"
        ),
        "body_html": None,
        "is_active": True,
    }


def sender():
    return {
        "id": 11,
        "display_name": "Nyumba Zetu Sales",
        "email_address": "sales@nyumbazetu.example",
        "reply_to_address": "replies@nyumbazetu.example",
        "provider": "smtp",
        "provider_reference": None,
        "is_default": True,
        "is_active": True,
    }


def send_payload(**overrides):
    from app.communications.message_schemas import MessageSendRequest

    values = {
        "lead_id": 41,
        "template_id": 9,
        "sender_identity_id": 11,
        "to_email": "alice@example.test",
        "subject": "Edited final subject",
        "body_text": "Edited final body",
        "idempotency_key": "lead-41-manual-001",
    }
    values.update(overrides)
    return MessageSendRequest(**values)


def test_send_requires_idempotency_key_and_rejects_secrets():
    from app.communications.message_schemas import MessageSendRequest

    with pytest.raises(ValidationError):
        MessageSendRequest(
            lead_id=41,
            sender_identity_id=11,
            to_email="alice@example.test",
            subject="Hello",
            body_text="Body",
        )

    with pytest.raises(ValidationError):
        MessageSendRequest(
            lead_id=41,
            sender_identity_id=11,
            to_email="alice@example.test",
            subject="Hello",
            body_text="Body",
            idempotency_key="manual-001",
            smtp_password="never-store-this",
        )


def test_preview_is_pure_and_uses_selected_template(monkeypatch):
    from app.communications import message_repository
    from app.communications.message_schemas import MessagePreviewRequest
    from app.communications.messages import preview_message

    monkeypatch.setattr(message_repository, "get_lead", lambda **_: lead())
    monkeypatch.setattr(
        message_repository,
        "get_template",
        lambda **_: template(),
    )
    monkeypatch.setattr(
        message_repository,
        "get_sender_identity",
        lambda **_: sender(),
    )

    result = preview_message(
        db=NoWriteDb(),
        payload=MessagePreviewRequest(
            lead_id=41,
            template_id=9,
            sender_identity_id=11,
        ),
        user=user(),
    )

    assert result["to_email"] == "alice@example.test"
    assert result["template_id"] == 9
    assert result["subject"] == "A better workflow for Example Properties"
    assert "Hi Alice" in result["body_text"]
    assert result["sender_identity"]["email_address"] == (
        "sales@nyumbazetu.example"
    )


def test_idempotency_returns_existing_without_provider_call(monkeypatch):
    from app.communications import message_repository
    from app.communications.messages import send_message

    existing = {
        "id": 77,
        "lead_id": 41,
        "template_id": 9,
        "sender_identity_id": 11,
        "recipient_email": "alice@example.test",
        "recipient_name": "Alice",
        "subject": "Already sent",
        "body_text": "Existing body",
        "body_html": None,
        "message_type": "manual",
        "status": "sent",
        "idempotency_key": "lead-41-manual-001",
        "provider_message_id": "provider-existing",
        "attachment_name": None,
        "attachment_content_type": None,
        "attachment_size": None,
        "follow_up_date": None,
        "error_message": None,
        "sent_at": None,
        "created_at": None,
        "updated_at": None,
    }
    monkeypatch.setattr(
        message_repository,
        "find_message_by_idempotency_key",
        lambda **_: existing,
    )

    provider = FakeProvider()
    result = send_message(
        db=SimpleNamespace(),
        payload=send_payload(),
        user=user(),
        provider=provider,
    )

    assert result == existing
    assert provider.calls == []


def test_suppression_and_staging_allowlist_are_enforced(monkeypatch):
    from app.communications import message_repository
    from app.communications.messages import (
        RecipientNotAllowedError,
        RecipientSuppressedError,
        enforce_delivery_policy,
        send_message,
    )

    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("EMAIL_DELIVERY_MODE", "allowlist")
    monkeypatch.setenv(
        "EMAIL_ALLOWED_RECIPIENTS",
        "allowed@example.test",
    )

    enforce_delivery_policy("allowed@example.test")
    with pytest.raises(RecipientNotAllowedError):
        enforce_delivery_policy("blocked@example.test")

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(
        message_repository,
        "find_message_by_idempotency_key",
        lambda **_: None,
    )
    monkeypatch.setattr(
        message_repository,
        "is_recipient_suppressed",
        lambda **_: True,
    )

    provider = FakeProvider()
    with pytest.raises(RecipientSuppressedError):
        send_message(
            db=SimpleNamespace(),
            payload=send_payload(),
            user=user(),
            provider=provider,
        )

    assert provider.calls == []


def test_success_uses_final_content_and_selected_sender(monkeypatch):
    from app.communications import message_repository
    from app.communications.messages import send_message

    events = []
    monkeypatch.setattr(
        message_repository,
        "find_message_by_idempotency_key",
        lambda **_: None,
    )
    monkeypatch.setattr(
        message_repository,
        "is_recipient_suppressed",
        lambda **_: False,
    )
    monkeypatch.setattr(
        message_repository,
        "get_sender_identity",
        lambda **_: sender(),
    )
    monkeypatch.setattr(
        message_repository,
        "create_queued_message",
        lambda **kwargs: {
            "id": 88,
            "status": "queued",
            **kwargs["values"],
        },
    )
    monkeypatch.setattr(
        message_repository,
        "mark_message_sent",
        lambda **kwargs: events.append(("sent", kwargs)),
    )
    monkeypatch.setattr(
        message_repository,
        "mark_message_failed",
        lambda **kwargs: events.append(("failed", kwargs)),
    )
    monkeypatch.setattr(
        message_repository,
        "update_lead_after_successful_send",
        lambda **kwargs: events.append(("lead", kwargs)),
    )
    monkeypatch.setattr(
        message_repository,
        "get_message",
        lambda **_: {
            "id": 88,
            "lead_id": 41,
            "template_id": 9,
            "sender_identity_id": 11,
            "recipient_email": "alice@example.test",
            "recipient_name": None,
            "subject": "Edited final subject",
            "body_text": "Edited final body",
            "body_html": None,
            "message_type": "manual",
            "status": "sent",
            "idempotency_key": "lead-41-manual-001",
            "provider_message_id": "provider-123",
            "attachment_name": None,
            "attachment_content_type": None,
            "attachment_size": None,
            "follow_up_date": None,
            "error_message": None,
            "sent_at": None,
            "created_at": None,
            "updated_at": None,
        },
    )

    db = SimpleNamespace(commit=lambda: events.append(("commit", {})))
    provider = FakeProvider()
    result = send_message(
        db=db,
        payload=send_payload(),
        user=user(),
        provider=provider,
    )

    assert result["status"] == "sent"
    outbound = provider.calls[0]
    assert outbound.subject == "Edited final subject"
    assert outbound.body_text == "Edited final body"
    assert outbound.from_email == "sales@nyumbazetu.example"
    assert outbound.reply_to == "replies@nyumbazetu.example"
    assert any(name == "lead" for name, _ in events)


def test_failed_send_does_not_update_lead(monkeypatch):
    from app.communications import message_repository
    from app.communications.delivery import ProviderResult
    from app.communications.messages import send_message

    events = []
    monkeypatch.setattr(
        message_repository,
        "find_message_by_idempotency_key",
        lambda **_: None,
    )
    monkeypatch.setattr(
        message_repository,
        "is_recipient_suppressed",
        lambda **_: False,
    )
    monkeypatch.setattr(
        message_repository,
        "get_sender_identity",
        lambda **_: sender(),
    )
    monkeypatch.setattr(
        message_repository,
        "create_queued_message",
        lambda **kwargs: {
            "id": 89,
            "status": "queued",
            **kwargs["values"],
        },
    )
    monkeypatch.setattr(
        message_repository,
        "mark_message_sent",
        lambda **kwargs: events.append(("sent", kwargs)),
    )
    monkeypatch.setattr(
        message_repository,
        "mark_message_failed",
        lambda **kwargs: events.append(("failed", kwargs)),
    )
    monkeypatch.setattr(
        message_repository,
        "update_lead_after_successful_send",
        lambda **kwargs: events.append(("lead", kwargs)),
    )
    monkeypatch.setattr(
        message_repository,
        "get_message",
        lambda **_: {
            "id": 89,
            "lead_id": 41,
            "template_id": 9,
            "sender_identity_id": 11,
            "recipient_email": "alice@example.test",
            "recipient_name": None,
            "subject": "Edited final subject",
            "body_text": "Edited final body",
            "body_html": None,
            "message_type": "manual",
            "status": "failed",
            "idempotency_key": "lead-41-manual-001",
            "provider_message_id": None,
            "attachment_name": None,
            "attachment_content_type": None,
            "attachment_size": None,
            "follow_up_date": None,
            "error_message": "Provider rejected",
            "sent_at": None,
            "created_at": None,
            "updated_at": None,
        },
    )

    db = SimpleNamespace(commit=lambda: events.append(("commit", {})))
    provider = FakeProvider(
        ProviderResult(
            accepted=False,
            error_message="Provider rejected",
        )
    )
    result = send_message(
        db=db,
        payload=send_payload(),
        user=user(),
        provider=provider,
    )

    assert result["status"] == "failed"
    assert any(name == "failed" for name, _ in events)
    assert not any(name == "lead" for name, _ in events)


def test_router_maps_suppression_and_is_mounted(monkeypatch):
    from pathlib import Path

    from app.communications.messages import RecipientSuppressedError
    from app.routers import communication_messages

    monkeypatch.setattr(
        communication_messages.message_service,
        "send_message",
        lambda **_: (_ for _ in ()).throw(
            RecipientSuppressedError("Recipient is suppressed")
        ),
    )

    with pytest.raises(HTTPException) as error:
        communication_messages.send_communication_message(
            payload=send_payload(),
            db=object(),
            user=user(),
        )

    assert error.value.status_code == 409

    source = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert (
        "from app.routers import communication_messages "
        "as communication_messages_router"
    ) in source
    assert (
        "app.include_router(communication_messages_router.router)"
        in source
    )
