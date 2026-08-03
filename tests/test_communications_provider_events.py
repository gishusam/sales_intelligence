import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.test")
os.environ.setdefault("EMAIL_WEBHOOK_SECRET", "webhook-secret")

from app.auth import CurrentUser


def user(role="sales"):
    return CurrentUser(
        id=3,
        name="Sales User",
        email="sales@example.test",
        role=role,
    )


def payload(**overrides):
    value = {
        "provider_event_id": "evt-001",
        "provider_message_id": "provider-message-123",
        "event_type": "delivered",
        "recipient_email": "alice@example.test",
        "occurred_at": datetime(
            2026,
            8,
            3,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        "bounce_type": None,
        "reason": None,
        "url": None,
        "metadata": {"source": "provider"},
    }
    value.update(overrides)
    return value


def message(**overrides):
    value = {
        "id": 901,
        "provider_message_id": "provider-message-123",
        "campaign_id": 21,
        "campaign_recipient_id": 501,
        "newsletter_id": None,
        "newsletter_recipient_id": None,
        "recipient_email": "alice@example.test",
        "message_type": "campaign",
        "status": "sent",
    }
    value.update(overrides)
    return value


def raw_body(values=None):
    values = values or payload()
    encoded = dict(values)
    encoded["occurred_at"] = encoded["occurred_at"].isoformat()
    return json.dumps(
        encoded,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_signature_roundtrip_and_tamper_detection():
    from app.communications.provider_event_service import (
        create_webhook_signature,
        verify_webhook_signature,
    )

    body = raw_body()
    signature = create_webhook_signature(
        body,
        secret="webhook-secret",
    )

    assert signature.startswith("sha256=")
    assert verify_webhook_signature(
        body,
        signature,
        secret="webhook-secret",
    )
    assert not verify_webhook_signature(
        body + b"tampered",
        signature,
        secret="webhook-secret",
    )


def test_missing_webhook_secret_fails_closed(monkeypatch):
    from app.communications.provider_event_service import (
        ProviderWebhookAuthenticationError,
        authenticate_webhook,
    )

    monkeypatch.delenv("EMAIL_WEBHOOK_SECRET", raising=False)

    with pytest.raises(
        ProviderWebhookAuthenticationError,
        match=r"not configured",
    ):
        authenticate_webhook(
            raw_body=raw_body(),
            signature="sha256=abc",
        )


def test_duplicate_provider_event_is_idempotent(monkeypatch):
    from app.communications import provider_event_repository as repository
    from app.communications.provider_event_schemas import ProviderEventPayload
    from app.communications.provider_event_service import process_event

    monkeypatch.setattr(
        repository,
        "find_event",
        lambda **kwargs: {
            "id": 1001,
            "provider": "generic",
            "provider_event_id": "evt-001",
            "status": "processed",
        },
    )
    monkeypatch.setattr(
        repository,
        "find_message_by_provider_id",
        lambda **kwargs: pytest.fail(
            "Duplicate event must not reapply side effects"
        ),
    )

    result = process_event(
        db=object(),
        provider="generic",
        event=ProviderEventPayload(**payload()),
        raw_payload=payload(),
    )

    assert result["duplicate"] is True
    assert result["event_id"] == 1001


def test_delivery_event_updates_message(monkeypatch):
    from app.communications import provider_event_repository as repository
    from app.communications.provider_event_schemas import ProviderEventPayload
    from app.communications.provider_event_service import process_event

    events = []

    monkeypatch.setattr(
        repository,
        "find_event",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "find_message_by_provider_id",
        lambda **kwargs: message(),
    )
    monkeypatch.setattr(
        repository,
        "create_event",
        lambda **kwargs: events.append(("event", kwargs)) or 1001,
    )
    monkeypatch.setattr(
        repository,
        "apply_message_event",
        lambda **kwargs: events.append(("message", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "update_recipient_from_event",
        lambda **kwargs: events.append(("recipient", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "suppress_email",
        lambda **kwargs: pytest.fail(
            "Delivery must not suppress recipient"
        ),
    )

    db = SimpleNamespace(
        commit=lambda: events.append(("commit", None)),
        rollback=lambda: events.append(("rollback", None)),
    )

    result = process_event(
        db=db,
        provider="generic",
        event=ProviderEventPayload(**payload()),
        raw_payload=payload(),
    )

    assert result["status"] == "processed"
    assert result["message_id"] == 901
    applied = next(
        item
        for kind, item in events
        if kind == "message"
    )
    assert applied["event_type"] == "delivered"


@pytest.mark.parametrize(
    ("event_type", "bounce_type", "should_suppress"),
    [
        ("hard_bounce", "hard", True),
        ("soft_bounce", "soft", False),
        ("complaint", None, True),
        ("unsubscribe", None, True),
    ],
)
def test_negative_events_apply_correct_suppression_policy(
    monkeypatch,
    event_type,
    bounce_type,
    should_suppress,
):
    from app.communications import provider_event_repository as repository
    from app.communications.provider_event_schemas import ProviderEventPayload
    from app.communications.provider_event_service import process_event

    suppressions = []

    monkeypatch.setattr(
        repository,
        "find_event",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "find_message_by_provider_id",
        lambda **kwargs: message(),
    )
    monkeypatch.setattr(
        repository,
        "create_event",
        lambda **kwargs: 1001,
    )
    monkeypatch.setattr(
        repository,
        "apply_message_event",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "update_recipient_from_event",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "suppress_email",
        lambda **kwargs: suppressions.append(kwargs),
    )

    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    values = payload(
        event_type=event_type,
        bounce_type=bounce_type,
        reason="Provider reported a negative event",
    )

    process_event(
        db=db,
        provider="generic",
        event=ProviderEventPayload(**values),
        raw_payload=values,
    )

    assert bool(suppressions) is should_suppress


def test_open_and_click_events_are_supported(monkeypatch):
    from app.communications import provider_event_repository as repository
    from app.communications.provider_event_schemas import ProviderEventPayload
    from app.communications.provider_event_service import process_event

    applied = []

    monkeypatch.setattr(repository, "find_event", lambda **kwargs: None)
    monkeypatch.setattr(
        repository,
        "find_message_by_provider_id",
        lambda **kwargs: message(),
    )
    monkeypatch.setattr(repository, "create_event", lambda **kwargs: 1001)
    monkeypatch.setattr(
        repository,
        "apply_message_event",
        lambda **kwargs: applied.append(kwargs),
    )
    monkeypatch.setattr(
        repository,
        "update_recipient_from_event",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "suppress_email",
        lambda **kwargs: None,
    )

    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    for event_type in ("opened", "clicked"):
        values = payload(
            provider_event_id=f"evt-{event_type}",
            event_type=event_type,
            url=(
                "https://nyumbazetu.example/listing"
                if event_type == "clicked"
                else None
            ),
        )
        process_event(
            db=db,
            provider="generic",
            event=ProviderEventPayload(**values),
            raw_payload=values,
        )

    assert [item["event_type"] for item in applied] == [
        "opened",
        "clicked",
    ]


def test_orphan_event_is_recorded_without_message_side_effects(
    monkeypatch,
):
    from app.communications import provider_event_repository as repository
    from app.communications.provider_event_schemas import ProviderEventPayload
    from app.communications.provider_event_service import process_event

    captured = []

    monkeypatch.setattr(repository, "find_event", lambda **kwargs: None)
    monkeypatch.setattr(
        repository,
        "find_message_by_provider_id",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "create_event",
        lambda **kwargs: captured.append(kwargs) or 1001,
    )
    monkeypatch.setattr(
        repository,
        "apply_message_event",
        lambda **kwargs: pytest.fail(
            "Orphan event has no message to update"
        ),
    )

    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    result = process_event(
        db=db,
        provider="generic",
        event=ProviderEventPayload(**payload()),
        raw_payload=payload(),
    )

    assert result["status"] == "orphaned"
    assert result["message_id"] is None
    assert captured[0]["email_message_id"] is None


def test_webhook_authentication_rejects_bad_signature():
    from app.communications.provider_event_service import (
        ProviderWebhookAuthenticationError,
        authenticate_webhook,
    )

    with pytest.raises(ProviderWebhookAuthenticationError):
        authenticate_webhook(
            raw_body=raw_body(),
            signature="sha256=bad",
        )


def test_sales_can_read_provider_events(monkeypatch):
    from app.routers import communication_provider_events

    monkeypatch.setattr(
        communication_provider_events.provider_event_repository,
        "list_events",
        lambda **kwargs: [{"id": 1001}],
    )

    result = communication_provider_events.list_provider_events(
        event_type=None,
        message_id=None,
        limit=100,
        db=object(),
        user=user("sales"),
    )

    assert result == [{"id": 1001}]


def test_main_mounts_provider_events_router():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert (
        "from app.routers import communication_provider_events "
        "as communication_provider_events_router"
    ) in source
    assert (
        "app.include_router("
        "communication_provider_events_router.router"
        ")"
    ) in source
