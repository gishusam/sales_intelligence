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
    "COMMUNICATIONS_WORKER_TOKEN",
    "test-worker-token",
)

from app.communications.delivery import ProviderResult


class FakeProvider:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or ProviderResult(
            accepted=True,
            provider_message_id="provider-123",
        )

    def send(self, message):
        self.calls.append(message)
        return self.result


def message(**overrides):
    value = {
        "id": 801,
        "campaign_id": 21,
        "campaign_recipient_id": 501,
        "campaign_step_id": 101,
        "lead_id": 1,
        "template_id": 9,
        "sender_identity_id": 11,
        "sent_by": 3,
        "recipient_email": "alice@example.test",
        "recipient_name": "Alice",
        "subject": "Stored final subject",
        "body_text": "Stored final body",
        "body_html": None,
        "message_type": "campaign",
        "status": "processing",
        "idempotency_key": "campaign:21:recipient:501:step:101",
        "attempt_count": 1,
        "scheduled_for": datetime.now(timezone.utc),
    }
    value.update(overrides)
    return value


def campaign(**overrides):
    value = {
        "id": 21,
        "status": "scheduled",
        "sender_identity_id": 11,
        "created_by": 3,
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


def recipient(**overrides):
    value = {
        "id": 501,
        "campaign_id": 21,
        "lead_id": 1,
        "recipient_email": "alice@example.test",
        "recipient_name": "Alice",
        "status": "queued",
        "company_name_snapshot": "Alpha Properties",
        "area_snapshot": "Kilimani",
        "rep_name_snapshot": "Admin User",
        "rep_email_snapshot": "admin@example.test",
    }
    value.update(overrides)
    return value


def next_step(**overrides):
    value = {
        "id": 102,
        "campaign_id": 21,
        "step_order": 2,
        "delay_days": 7,
        "template_id": 10,
        "snapshot_subject": "Following up with {company_name}",
        "snapshot_body_text": (
            "Hi {contact_name},\n"
            "Regards,\n{rep_name}\n{rep_email}"
        ),
        "snapshot_body_html": None,
    }
    value.update(overrides)
    return value


def test_worker_request_bounds_batch_size():
    from app.communications.delivery_worker_schemas import WorkerRunRequest

    with pytest.raises(ValidationError):
        WorkerRunRequest(batch_size=0)

    with pytest.raises(ValidationError):
        WorkerRunRequest(batch_size=501)

    assert WorkerRunRequest(batch_size=25).batch_size == 25


def test_retry_backoff_is_exponential_and_bounded():
    from app.communications.delivery_worker import retry_delay

    assert retry_delay(1) == timedelta(minutes=1)
    assert retry_delay(2) == timedelta(minutes=2)
    assert retry_delay(3) == timedelta(minutes=4)
    assert retry_delay(20) == timedelta(hours=6)


def test_paused_campaign_is_deferred_without_sending(monkeypatch):
    from app.communications import delivery_worker_repository as repository
    from app.communications.delivery_worker import process_claimed_message

    events = []
    monkeypatch.setattr(
        repository,
        "get_campaign",
        lambda **kwargs: campaign(status="paused"),
    )
    monkeypatch.setattr(
        repository,
        "release_message",
        lambda **kwargs: events.append(("released", kwargs)),
    )

    provider = FakeProvider()
    db = SimpleNamespace(
        commit=lambda: events.append(("commit", None)),
        rollback=lambda: events.append(("rollback", None)),
    )

    result = process_claimed_message(
        db=db,
        message=message(),
        provider=provider,
        max_attempts=5,
        now=datetime.now(timezone.utc),
    )

    assert result == "deferred"
    assert provider.calls == []
    assert events[0][0] == "released"


def test_cancelled_campaign_cancels_job(monkeypatch):
    from app.communications import delivery_worker_repository as repository
    from app.communications.delivery_worker import process_claimed_message

    events = []
    monkeypatch.setattr(
        repository,
        "get_campaign",
        lambda **kwargs: campaign(status="cancelled"),
    )
    monkeypatch.setattr(
        repository,
        "mark_message_cancelled",
        lambda **kwargs: events.append(("cancelled", kwargs)),
    )

    provider = FakeProvider()
    db = SimpleNamespace(
        commit=lambda: events.append(("commit", None)),
        rollback=lambda: None,
    )

    result = process_claimed_message(
        db=db,
        message=message(),
        provider=provider,
        max_attempts=5,
        now=datetime.now(timezone.utc),
    )

    assert result == "cancelled"
    assert provider.calls == []
    assert events[0][0] == "cancelled"


def test_suppression_is_rechecked_before_provider(monkeypatch):
    from app.communications import delivery_worker_repository as repository
    from app.communications.delivery_worker import process_claimed_message

    events = []
    monkeypatch.setattr(
        repository,
        "get_campaign",
        lambda **kwargs: campaign(),
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
        "finalize_campaign_if_complete",
        lambda **kwargs: events.append(("finalize", kwargs)),
    )

    provider = FakeProvider()
    db = SimpleNamespace(
        commit=lambda: events.append(("commit", None)),
        rollback=lambda: None,
    )

    result = process_claimed_message(
        db=db,
        message=message(),
        provider=provider,
        max_attempts=5,
        now=datetime.now(timezone.utc),
    )

    assert result == "suppressed"
    assert provider.calls == []
    assert events[0][0] == "message"
    assert events[1][0] == "recipient"


def test_success_sends_persisted_content_and_queues_next_step(monkeypatch):
    from app.communications import delivery_worker_repository as repository
    from app.communications.delivery_worker import process_claimed_message

    events = []
    queued = []

    monkeypatch.setattr(repository, "get_campaign", lambda **kwargs: campaign())
    monkeypatch.setattr(repository, "is_suppressed", lambda **kwargs: False)
    monkeypatch.setattr(repository, "get_sender_identity", lambda **kwargs: sender())
    monkeypatch.setattr(
        repository,
        "mark_message_sent",
        lambda **kwargs: events.append(("sent", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "update_lead_after_successful_send",
        lambda **kwargs: events.append(("lead", kwargs)),
    )
    monkeypatch.setattr(repository, "get_recipient", lambda **kwargs: recipient())
    monkeypatch.setattr(repository, "get_next_frozen_step", lambda **kwargs: next_step())
    monkeypatch.setattr(
        repository,
        "queue_next_step_message",
        lambda **kwargs: queued.append(kwargs) or True,
    )
    monkeypatch.setattr(
        repository,
        "mark_recipient_next_step",
        lambda **kwargs: events.append(("next", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "mark_campaign_running",
        lambda **kwargs: events.append(("running", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "finalize_campaign_if_complete",
        lambda **kwargs: events.append(("finalize", kwargs)),
    )

    provider = FakeProvider()
    db = SimpleNamespace(
        commit=lambda: events.append(("commit", None)),
        rollback=lambda: events.append(("rollback", None)),
    )
    now = datetime.now(timezone.utc)

    result = process_claimed_message(
        db=db,
        message=message(),
        provider=provider,
        max_attempts=5,
        now=now,
    )

    assert result == "sent"
    assert len(provider.calls) == 1
    outbound = provider.calls[0]
    assert outbound.subject == "Stored final subject"
    assert outbound.body_text == "Stored final body"
    assert outbound.from_email == "sales@nyumbazetu.example"

    assert len(queued) == 1
    job = queued[0]
    assert job["idempotency_key"] == (
        "campaign:21:recipient:501:step:102"
    )
    assert job["subject"] == "Following up with Alpha Properties"
    assert "Hi Alice" in job["body_text"]
    assert job["scheduled_for"] == now + timedelta(days=7)


def test_final_step_marks_recipient_complete(monkeypatch):
    from app.communications import delivery_worker_repository as repository
    from app.communications.delivery_worker import process_claimed_message

    events = []
    monkeypatch.setattr(repository, "get_campaign", lambda **kwargs: campaign(status="running"))
    monkeypatch.setattr(repository, "is_suppressed", lambda **kwargs: False)
    monkeypatch.setattr(repository, "get_sender_identity", lambda **kwargs: sender())
    monkeypatch.setattr(repository, "mark_message_sent", lambda **kwargs: None)
    monkeypatch.setattr(repository, "update_lead_after_successful_send", lambda **kwargs: None)
    monkeypatch.setattr(repository, "get_recipient", lambda **kwargs: recipient())
    monkeypatch.setattr(repository, "get_next_frozen_step", lambda **kwargs: None)
    monkeypatch.setattr(
        repository,
        "mark_recipient_complete",
        lambda **kwargs: events.append(("complete", kwargs)),
    )
    monkeypatch.setattr(repository, "mark_campaign_running", lambda **kwargs: None)
    monkeypatch.setattr(
        repository,
        "finalize_campaign_if_complete",
        lambda **kwargs: events.append(("finalize", kwargs)),
    )

    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    result = process_claimed_message(
        db=db,
        message=message(campaign_step_id=102),
        provider=FakeProvider(),
        max_attempts=5,
        now=datetime.now(timezone.utc),
    )

    assert result == "sent"
    assert any(event[0] == "complete" for event in events)
    assert any(event[0] == "finalize" for event in events)


def test_transient_failure_retries(monkeypatch):
    from app.communications import delivery_worker_repository as repository
    from app.communications.delivery_worker import process_claimed_message

    events = []
    monkeypatch.setattr(repository, "get_campaign", lambda **kwargs: campaign())
    monkeypatch.setattr(repository, "is_suppressed", lambda **kwargs: False)
    monkeypatch.setattr(repository, "get_sender_identity", lambda **kwargs: sender())
    monkeypatch.setattr(
        repository,
        "mark_message_retry",
        lambda **kwargs: events.append(("retry", kwargs)),
    )

    provider = FakeProvider(
        ProviderResult(
            accepted=False,
            error_message="Temporary provider failure",
        )
    )
    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
    now = datetime.now(timezone.utc)

    result = process_claimed_message(
        db=db,
        message=message(attempt_count=2),
        provider=provider,
        max_attempts=5,
        now=now,
    )

    assert result == "retry"
    assert events[0][1]["next_attempt_at"] == now + timedelta(minutes=2)


def test_exhausted_failure_dead_letters(monkeypatch):
    from app.communications import delivery_worker_repository as repository
    from app.communications.delivery_worker import process_claimed_message

    events = []
    monkeypatch.setattr(repository, "get_campaign", lambda **kwargs: campaign())
    monkeypatch.setattr(repository, "is_suppressed", lambda **kwargs: False)
    monkeypatch.setattr(repository, "get_sender_identity", lambda **kwargs: sender())
    monkeypatch.setattr(
        repository,
        "mark_message_dead_letter",
        lambda **kwargs: events.append(("dead", kwargs)),
    )
    monkeypatch.setattr(
        repository,
        "mark_recipient_failed",
        lambda **kwargs: events.append(("recipient", kwargs)),
    )
    monkeypatch.setattr(repository, "finalize_campaign_if_complete", lambda **kwargs: None)

    provider = FakeProvider(
        ProviderResult(
            accepted=False,
            error_message="Permanent provider failure",
        )
    )
    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    result = process_claimed_message(
        db=db,
        message=message(attempt_count=5),
        provider=provider,
        max_attempts=5,
        now=datetime.now(timezone.utc),
    )

    assert result == "dead_letter"
    assert events[0][0] == "dead"
    assert events[1][0] == "recipient"


def test_worker_run_recovers_stale_jobs_and_claims_bounded_batch(monkeypatch):
    from app.communications import delivery_worker_repository as repository
    from app.communications.delivery_worker import run_delivery_worker

    events = []
    monkeypatch.setattr(
        repository,
        "requeue_stale_processing",
        lambda **kwargs: events.append(("recovered", kwargs)) or 2,
    )
    monkeypatch.setattr(
        repository,
        "claim_due_messages",
        lambda **kwargs: events.append(("claimed", kwargs)) or [],
    )

    result = run_delivery_worker(
        db=SimpleNamespace(commit=lambda: None, rollback=lambda: None),
        provider=FakeProvider(),
        batch_size=25,
        worker_id="worker-test",
        max_attempts=5,
        lock_timeout_minutes=15,
        now=datetime.now(timezone.utc),
    )

    assert result["recovered"] == 2
    assert result["claimed"] == 0
    assert events[1][1]["limit"] == 25
    assert events[1][1]["worker_id"] == "worker-test"


def test_worker_endpoint_requires_valid_token():
    from app.routers import communication_delivery_worker

    with pytest.raises(HTTPException) as error:
        communication_delivery_worker.run_worker(
            payload=SimpleNamespace(batch_size=10),
            x_worker_token=None,
            db=object(),
        )
    assert error.value.status_code == 401

    with pytest.raises(HTTPException) as error:
        communication_delivery_worker.run_worker(
            payload=SimpleNamespace(batch_size=10),
            x_worker_token="wrong-token",
            db=object(),
        )
    assert error.value.status_code == 401


def test_main_mounts_delivery_worker_router():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert (
        "from app.routers import communication_delivery_worker "
        "as communication_delivery_worker_router"
    ) in source
    assert (
        "app.include_router(communication_delivery_worker_router.router)"
        in source
    )
