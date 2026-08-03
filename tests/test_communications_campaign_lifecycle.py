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

from app.auth import CurrentUser


def user(role="admin"):
    return CurrentUser(
        id=3,
        name="Admin User",
        email="admin@example.test",
        role=role,
    )


def campaign(**overrides):
    value = {
        "id": 21,
        "name": "Kilimani agency outreach",
        "campaign_type": "cold",
        "sender_identity_id": 11,
        "status": "draft",
        "created_by": 3,
        "scheduled_for": None,
        "prepared_at": None,
        "started_at": None,
        "paused_at": None,
        "cancelled_at": None,
        "paused_from_status": None,
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


def steps():
    return [
        {
            "id": 101,
            "campaign_id": 21,
            "step_order": 1,
            "delay_days": 0,
            "template_id": 9,
            "template_type": "cold",
            "template_is_active": True,
            "template_version": 2,
            "template_subject": "Hello {company_name}",
            "template_body_text": (
                "Hi {contact_name},\n"
                "Regards,\n{rep_name}\n{rep_email}"
            ),
            "template_body_html": None,
            "snapshot_subject": None,
            "snapshot_body_text": None,
            "snapshot_body_html": None,
            "snapshot_template_version": None,
        },
        {
            "id": 102,
            "campaign_id": 21,
            "step_order": 2,
            "delay_days": 7,
            "template_id": 10,
            "template_type": "cold",
            "template_is_active": True,
            "template_version": 1,
            "template_subject": "Following up with {company_name}",
            "template_body_text": (
                "Hi {contact_name},\n"
                "Regards,\n{rep_name}\n{rep_email}"
            ),
            "template_body_html": None,
            "snapshot_subject": None,
            "snapshot_body_text": None,
            "snapshot_body_html": None,
            "snapshot_template_version": None,
        },
    ]


def recipients():
    return [
        {
            "id": 501,
            "campaign_id": 21,
            "lead_id": 1,
            "recipient_email": "alice@example.test",
            "recipient_name": "Alice",
            "status": "enrolled",
            "company_name_snapshot": "Alpha Properties",
            "area_snapshot": "Kilimani",
            "rep_name_snapshot": "Admin User",
            "rep_email_snapshot": "admin@example.test",
        },
        {
            "id": 502,
            "campaign_id": 21,
            "lead_id": 2,
            "recipient_email": "blocked@example.test",
            "recipient_name": "Blocked",
            "status": "enrolled",
            "company_name_snapshot": "Blocked Properties",
            "area_snapshot": "Westlands",
            "rep_name_snapshot": "Admin User",
            "rep_email_snapshot": "admin@example.test",
        },
    ]


def test_transition_policy_accepts_only_supported_paths():
    from app.communications.campaign_lifecycle import (
        CampaignStateError,
        validate_transition,
    )

    allowed = {
        ("draft", "ready"),
        ("ready", "scheduled"),
        ("scheduled", "paused"),
        ("running", "paused"),
        ("paused", "scheduled"),
        ("paused", "running"),
        ("scheduled", "running"),
        ("running", "completed"),
        ("draft", "cancelled"),
        ("ready", "cancelled"),
        ("scheduled", "cancelled"),
        ("running", "cancelled"),
        ("paused", "cancelled"),
    }

    for current, target in allowed:
        validate_transition(current, target)

    with pytest.raises(
        CampaignStateError,
        match=r"Invalid campaign transition",
    ):
        validate_transition("draft", "running")


def test_schedule_request_requires_timezone_aware_datetime():
    from app.communications.campaign_lifecycle_schemas import (
        CampaignScheduleRequest,
    )

    with pytest.raises(
        ValidationError,
        match=r"timezone-aware",
    ):
        CampaignScheduleRequest(
            scheduled_for=datetime(2026, 8, 4, 9, 0),
        )


def test_prepare_rejects_missing_steps(monkeypatch):
    from app.communications import campaign_execution_repository as repository
    from app.communications.campaign_lifecycle import (
        CampaignPreflightError,
        prepare_campaign,
    )

    monkeypatch.setattr(
        repository,
        "get_campaign",
        lambda **kwargs: campaign(),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "list_steps_for_preflight",
        lambda **kwargs: [],
    )

    with pytest.raises(
        CampaignPreflightError,
        match=r"at least one step",
    ):
        prepare_campaign(
            db=object(),
            campaign_id=21,
            user=user(),
        )


def test_prepare_rejects_non_contiguous_steps(monkeypatch):
    from app.communications import campaign_execution_repository as repository
    from app.communications.campaign_lifecycle import (
        CampaignPreflightError,
        prepare_campaign,
    )

    bad_steps = steps()
    bad_steps[1]["step_order"] = 3

    monkeypatch.setattr(
        repository,
        "get_campaign",
        lambda **kwargs: campaign(),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "list_steps_for_preflight",
        lambda **kwargs: bad_steps,
    )

    with pytest.raises(
        CampaignPreflightError,
        match=r"contiguous",
    ):
        prepare_campaign(
            db=object(),
            campaign_id=21,
            user=user(),
        )


def test_prepare_freezes_steps_and_suppresses_recipients(
    monkeypatch,
):
    from app.communications import campaign_execution_repository as repository
    from app.communications.campaign_lifecycle import prepare_campaign

    events = []

    monkeypatch.setattr(
        repository,
        "get_campaign",
        lambda **kwargs: campaign(),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "list_steps_for_preflight",
        lambda **kwargs: steps(),
    )
    monkeypatch.setattr(
        repository,
        "freeze_step_snapshot",
        lambda **kwargs: events.append(
            ("step_frozen", kwargs["step_id"])
        ),
    )
    monkeypatch.setattr(
        repository,
        "freeze_recipient_personalization",
        lambda **kwargs: events.append(
            ("recipients_frozen", kwargs["campaign_id"])
        ),
    )
    monkeypatch.setattr(
        repository,
        "list_enrolled_recipients",
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
        lambda **kwargs: events.append(
            ("suppressed", kwargs["recipient_id"])
        ),
    )
    monkeypatch.setattr(
        repository,
        "mark_campaign_ready",
        lambda **kwargs: events.append(
            ("ready", kwargs["campaign_id"])
        ),
    )

    db = SimpleNamespace(
        commit=lambda: events.append(("commit", None)),
        rollback=lambda: events.append(("rollback", None)),
    )

    result = prepare_campaign(
        db=db,
        campaign_id=21,
        user=user(),
    )

    assert result["status"] == "ready"
    assert result["step_count"] == 2
    assert result["eligible_recipients"] == 1
    assert result["suppressed_recipients"] == 1
    assert ("step_frozen", 101) in events
    assert ("step_frozen", 102) in events
    assert ("suppressed", 502) in events
    assert ("ready", 21) in events
    assert ("commit", None) in events


def test_prepare_rejects_campaign_with_no_eligible_recipients(
    monkeypatch,
):
    from app.communications import campaign_execution_repository as repository
    from app.communications.campaign_lifecycle import (
        CampaignPreflightError,
        prepare_campaign,
    )

    only_blocked = [recipients()[1]]

    monkeypatch.setattr(
        repository,
        "get_campaign",
        lambda **kwargs: campaign(),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "list_steps_for_preflight",
        lambda **kwargs: steps(),
    )
    monkeypatch.setattr(
        repository,
        "freeze_step_snapshot",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "freeze_recipient_personalization",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "list_enrolled_recipients",
        lambda **kwargs: only_blocked,
    )
    monkeypatch.setattr(
        repository,
        "is_suppressed",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        repository,
        "mark_recipient_suppressed",
        lambda **kwargs: None,
    )

    db = SimpleNamespace(
        commit=lambda: None,
        rollback=lambda: None,
    )

    with pytest.raises(
        CampaignPreflightError,
        match=r"No eligible recipients remain",
    ):
        prepare_campaign(
            db=db,
            campaign_id=21,
            user=user(),
        )


def test_schedule_creates_idempotent_first_step_jobs(
    monkeypatch,
):
    from app.communications import campaign_execution_repository as repository
    from app.communications.campaign_lifecycle import schedule_campaign
    from app.communications.campaign_lifecycle_schemas import (
        CampaignScheduleRequest,
    )

    frozen_step = {
        **steps()[0],
        "snapshot_subject": "Hello {company_name}",
        "snapshot_body_text": (
            "Hi {contact_name},\n"
            "Regards,\n{rep_name}\n{rep_email}"
        ),
        "snapshot_body_html": None,
        "snapshot_template_version": 2,
    }
    active_recipient = recipients()[0]
    queued = []
    events = []

    monkeypatch.setattr(
        repository,
        "get_campaign",
        lambda **kwargs: campaign(status="ready"),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "get_first_frozen_step",
        lambda **kwargs: frozen_step,
    )
    monkeypatch.setattr(
        repository,
        "list_eligible_recipients",
        lambda **kwargs: [active_recipient],
    )
    monkeypatch.setattr(
        repository,
        "is_suppressed",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        repository,
        "queue_campaign_message",
        lambda **kwargs: queued.append(kwargs) or True,
    )
    monkeypatch.setattr(
        repository,
        "mark_recipient_queued",
        lambda **kwargs: events.append(
            ("recipient_queued", kwargs["recipient_id"])
        ),
    )
    monkeypatch.setattr(
        repository,
        "mark_campaign_scheduled",
        lambda **kwargs: events.append(
            ("scheduled", kwargs["campaign_id"])
        ),
    )

    db = SimpleNamespace(
        commit=lambda: events.append(("commit", None)),
        rollback=lambda: events.append(("rollback", None)),
    )
    scheduled_for = datetime.now(timezone.utc) + timedelta(hours=2)

    result = schedule_campaign(
        db=db,
        campaign_id=21,
        payload=CampaignScheduleRequest(
            scheduled_for=scheduled_for,
        ),
        user=user(),
    )

    assert result["status"] == "scheduled"
    assert result["queued_messages"] == 1
    assert len(queued) == 1

    job = queued[0]
    assert job["idempotency_key"] == (
        "campaign:21:recipient:501:step:101"
    )
    assert job["subject"] == "Hello Alpha Properties"
    assert "Hi Alice" in job["body_text"]
    assert job["scheduled_for"] == scheduled_for
    assert ("recipient_queued", 501) in events
    assert ("scheduled", 21) in events


def test_schedule_is_idempotent_when_job_already_exists(
    monkeypatch,
):
    from app.communications import campaign_execution_repository as repository
    from app.communications.campaign_lifecycle import schedule_campaign
    from app.communications.campaign_lifecycle_schemas import (
        CampaignScheduleRequest,
    )

    frozen_step = {
        **steps()[0],
        "snapshot_subject": "Hello {company_name}",
        "snapshot_body_text": (
            "Hi {contact_name},\n"
            "Regards,\n{rep_name}\n{rep_email}"
        ),
        "snapshot_body_html": None,
        "snapshot_template_version": 2,
    }
    queued_updates = []

    monkeypatch.setattr(
        repository,
        "get_campaign",
        lambda **kwargs: campaign(status="ready"),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "get_first_frozen_step",
        lambda **kwargs: frozen_step,
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
        "queue_campaign_message",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        repository,
        "mark_recipient_queued",
        lambda **kwargs: queued_updates.append(kwargs),
    )
    monkeypatch.setattr(
        repository,
        "mark_campaign_scheduled",
        lambda **kwargs: None,
    )

    db = SimpleNamespace(
        commit=lambda: None,
        rollback=lambda: None,
    )

    result = schedule_campaign(
        db=db,
        campaign_id=21,
        payload=CampaignScheduleRequest(
            scheduled_for=(
                datetime.now(timezone.utc)
                + timedelta(hours=1)
            ),
        ),
        user=user(),
    )

    assert result["queued_messages"] == 0
    assert result["existing_messages"] == 1
    assert queued_updates == []


def test_schedule_never_calls_email_provider(monkeypatch):
    from app.communications import campaign_execution_repository as repository
    from app.communications.campaign_lifecycle import schedule_campaign
    from app.communications.campaign_lifecycle_schemas import (
        CampaignScheduleRequest,
    )
    from app.communications.smtp_provider import SMTPEmailProvider

    frozen_step = {
        **steps()[0],
        "snapshot_subject": "Hello {company_name}",
        "snapshot_body_text": (
            "Hi {contact_name},\n"
            "Regards,\n{rep_name}\n{rep_email}"
        ),
        "snapshot_body_html": None,
        "snapshot_template_version": 2,
    }

    monkeypatch.setattr(
        repository,
        "get_campaign",
        lambda **kwargs: campaign(status="ready"),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "get_first_frozen_step",
        lambda **kwargs: frozen_step,
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
        "queue_campaign_message",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        repository,
        "mark_recipient_queued",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "mark_campaign_scheduled",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        SMTPEmailProvider,
        "send",
        lambda *args, **kwargs: pytest.fail(
            "Scheduling must not deliver email"
        ),
    )

    db = SimpleNamespace(
        commit=lambda: None,
        rollback=lambda: None,
    )

    result = schedule_campaign(
        db=db,
        campaign_id=21,
        payload=CampaignScheduleRequest(
            scheduled_for=datetime.now(timezone.utc),
        ),
        user=user(),
    )

    assert result["queued_messages"] == 1


def test_pause_resume_and_cancel_follow_state_policy(
    monkeypatch,
):
    from app.communications import campaign_execution_repository as repository
    from app.communications.campaign_lifecycle import (
        cancel_campaign,
        pause_campaign,
        resume_campaign,
    )

    events = []

    monkeypatch.setattr(
        repository,
        "get_campaign",
        lambda **kwargs: campaign(status="scheduled"),
    )
    monkeypatch.setattr(
        repository,
        "mark_campaign_paused",
        lambda **kwargs: events.append(("paused", kwargs)),
    )

    db = SimpleNamespace(
        commit=lambda: events.append(("commit", None)),
        rollback=lambda: None,
    )

    paused = pause_campaign(
        db=db,
        campaign_id=21,
        user=user(),
    )

    assert paused["status"] == "paused"

    monkeypatch.setattr(
        repository,
        "get_campaign",
        lambda **kwargs: campaign(
            status="paused",
            paused_from_status="scheduled",
        ),
    )
    monkeypatch.setattr(
        repository,
        "mark_campaign_resumed",
        lambda **kwargs: events.append(("resumed", kwargs)),
    )

    resumed = resume_campaign(
        db=db,
        campaign_id=21,
        user=user(),
    )

    assert resumed["status"] == "scheduled"

    monkeypatch.setattr(
        repository,
        "get_campaign",
        lambda **kwargs: campaign(status="scheduled"),
    )
    monkeypatch.setattr(
        repository,
        "cancel_campaign_and_pending_jobs",
        lambda **kwargs: events.append(("cancelled", kwargs)),
    )

    cancelled = cancel_campaign(
        db=db,
        campaign_id=21,
        user=user(),
    )

    assert cancelled["status"] == "cancelled"


def test_sales_cannot_prepare_or_schedule(monkeypatch):
    from app.communications.campaign_lifecycle_schemas import (
        CampaignScheduleRequest,
    )
    from app.routers import communication_campaign_lifecycle

    called = []

    monkeypatch.setattr(
        communication_campaign_lifecycle.lifecycle_service,
        "prepare_campaign",
        lambda **kwargs: called.append("prepare"),
    )

    with pytest.raises(HTTPException) as error:
        communication_campaign_lifecycle.prepare_campaign(
            campaign_id=21,
            db=object(),
            user=user("sales"),
        )

    assert error.value.status_code == 403
    assert called == []

    with pytest.raises(HTTPException) as error:
        communication_campaign_lifecycle.schedule_campaign(
            campaign_id=21,
            payload=CampaignScheduleRequest(
                scheduled_for=datetime.now(timezone.utc),
            ),
            db=object(),
            user=user("sales"),
        )

    assert error.value.status_code == 403


def test_main_mounts_campaign_lifecycle_router():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert (
        "from app.routers import communication_campaign_lifecycle "
        "as communication_campaign_lifecycle_router"
    ) in source
    assert (
        "app.include_router("
        "communication_campaign_lifecycle_router.router"
        ")"
    ) in source
