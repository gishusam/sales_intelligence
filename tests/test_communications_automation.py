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


def rule(**overrides):
    value = {
        "id": 31,
        "name": "Seven-day follow-up",
        "trigger_type": "inactivity_followup",
        "template_id": 10,
        "sender_identity_id": 11,
        "inactivity_days": 7,
        "max_drafts_per_lead": 1,
        "is_active": True,
        "created_by": 3,
        "updated_by": 3,
        "created_at": None,
        "updated_at": None,
    }
    value.update(overrides)
    return value


def template(**overrides):
    value = {
        "id": 10,
        "template_type": "followup",
        "subject": "Following up with {company_name}",
        "body_text": (
            "Hi {contact_name},\n"
            "I wanted to follow up about {company_name} in {area}.\n"
            "Regards,\n{rep_name}\n{rep_email}"
        ),
        "body_html": None,
        "version": 2,
        "is_active": True,
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


def lead(**overrides):
    value = {
        "id": 41,
        "name": "Alpha Properties",
        "contact_person": "Alice",
        "owner_name": None,
        "email": "alice@example.test",
        "area": "Kilimani",
        "status": "contacted",
        "last_contacted": datetime(2026, 7, 20, tzinfo=timezone.utc),
        "reply_received_at": None,
        "automation_paused": False,
    }
    value.update(overrides)
    return value


def test_rule_defaults_to_inactive():
    from app.communications.automation_schemas import AutomationRuleCreate

    created = AutomationRuleCreate(
        name="Seven-day follow-up",
        template_id=10,
        sender_identity_id=11,
        inactivity_days=7,
    )

    assert created.trigger_type == "inactivity_followup"
    assert created.is_active is False


def test_rule_rejects_zero_inactivity_days():
    from app.communications.automation_schemas import AutomationRuleCreate

    with pytest.raises(ValidationError):
        AutomationRuleCreate(
            name="Immediate follow-up",
            template_id=10,
            sender_identity_id=11,
            inactivity_days=0,
        )


def test_rule_update_requires_a_field():
    from app.communications.automation_schemas import AutomationRuleUpdate

    with pytest.raises(
        ValidationError,
        match=r"At least one automation rule field must be supplied",
    ):
        AutomationRuleUpdate()


def test_rule_requires_active_followup_template(monkeypatch):
    from app.communications import automation_repository as repository
    from app.communications.automation_schemas import AutomationRuleCreate
    from app.communications.automation_service import (
        AutomationValidationError,
        create_rule,
    )

    monkeypatch.setattr(
        repository,
        "get_template",
        lambda **kwargs: template(template_type="cold"),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )

    with pytest.raises(
        AutomationValidationError,
        match=r"follow-up template",
    ):
        create_rule(
            db=object(),
            payload=AutomationRuleCreate(
                name="Seven-day follow-up",
                template_id=10,
                sender_identity_id=11,
                inactivity_days=7,
            ),
            user=user(),
        )


def test_evaluation_creates_reviewable_draft_only(monkeypatch):
    from app.communications import automation_repository as repository
    from app.communications.automation_service import evaluate_rule

    events = []

    monkeypatch.setattr(
        repository,
        "get_rule",
        lambda **kwargs: rule(),
    )
    monkeypatch.setattr(
        repository,
        "get_template",
        lambda **kwargs: template(),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "list_candidate_leads",
        lambda **kwargs: [lead()],
    )
    monkeypatch.setattr(
        repository,
        "is_suppressed",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        repository,
        "count_rule_drafts_for_lead",
        lambda **kwargs: 0,
    )
    monkeypatch.setattr(
        repository,
        "find_execution_by_key",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "create_automation_draft",
        lambda **kwargs: events.append(
            ("draft", kwargs)
        ) or 901,
    )
    monkeypatch.setattr(
        repository,
        "record_execution",
        lambda **kwargs: events.append(
            ("execution", kwargs)
        ),
    )

    db = SimpleNamespace(
        commit=lambda: events.append(("commit", None)),
        rollback=lambda: events.append(("rollback", None)),
    )

    result = evaluate_rule(
        db=db,
        rule_id=31,
        batch_size=50,
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    assert result["drafted"] == 1
    assert result["skipped"] == 0

    draft = next(
        event[1]
        for event in events
        if event[0] == "draft"
    )

    assert draft["status"] == "draft"
    assert draft["message_type"] == "automation"
    assert draft["subject"] == "Following up with Alpha Properties"
    assert "Hi Alice" in draft["body_text"]
    assert draft["idempotency_key"].startswith(
        "automation:31:lead:41:"
    )


def test_evaluation_skips_suppressed_replied_paused_and_closed(
    monkeypatch,
):
    from app.communications import automation_repository as repository
    from app.communications.automation_service import evaluate_rule

    candidates = [
        lead(id=1, email="blocked@example.test"),
        lead(
            id=2,
            email="replied@example.test",
            reply_received_at=datetime(
                2026,
                8,
                1,
                tzinfo=timezone.utc,
            ),
        ),
        lead(
            id=3,
            email="paused@example.test",
            automation_paused=True,
        ),
        lead(
            id=4,
            email="won@example.test",
            status="won",
        ),
    ]
    executions = []

    monkeypatch.setattr(
        repository,
        "get_rule",
        lambda **kwargs: rule(),
    )
    monkeypatch.setattr(
        repository,
        "get_template",
        lambda **kwargs: template(),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "list_candidate_leads",
        lambda **kwargs: candidates,
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
        "count_rule_drafts_for_lead",
        lambda **kwargs: 0,
    )
    monkeypatch.setattr(
        repository,
        "find_execution_by_key",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "create_automation_draft",
        lambda **kwargs: pytest.fail(
            "Ineligible leads must not create drafts"
        ),
    )
    monkeypatch.setattr(
        repository,
        "record_execution",
        lambda **kwargs: executions.append(kwargs),
    )

    db = SimpleNamespace(
        commit=lambda: None,
        rollback=lambda: None,
    )

    result = evaluate_rule(
        db=db,
        rule_id=31,
        batch_size=50,
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    assert result["drafted"] == 0
    assert result["skipped"] == 4

    reasons = {item["reason"] for item in executions}
    assert reasons == {
        "suppressed",
        "reply_received",
        "automation_paused",
        "closed_lead",
    }


def test_evaluation_is_idempotent(monkeypatch):
    from app.communications import automation_repository as repository
    from app.communications.automation_service import evaluate_rule

    monkeypatch.setattr(
        repository,
        "get_rule",
        lambda **kwargs: rule(),
    )
    monkeypatch.setattr(
        repository,
        "get_template",
        lambda **kwargs: template(),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "list_candidate_leads",
        lambda **kwargs: [lead()],
    )
    monkeypatch.setattr(
        repository,
        "find_execution_by_key",
        lambda **kwargs: {
            "id": 701,
            "status": "drafted",
        },
    )
    monkeypatch.setattr(
        repository,
        "create_automation_draft",
        lambda **kwargs: pytest.fail(
            "Existing executions must not duplicate drafts"
        ),
    )

    db = SimpleNamespace(
        commit=lambda: None,
        rollback=lambda: None,
    )

    result = evaluate_rule(
        db=db,
        rule_id=31,
        batch_size=50,
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    assert result["existing"] == 1
    assert result["drafted"] == 0


def test_automation_evaluation_never_calls_provider(monkeypatch):
    from app.communications import automation_repository as repository
    from app.communications.automation_service import evaluate_rule
    from app.communications.smtp_provider import SMTPEmailProvider

    monkeypatch.setattr(
        repository,
        "get_rule",
        lambda **kwargs: rule(),
    )
    monkeypatch.setattr(
        repository,
        "get_template",
        lambda **kwargs: template(),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "list_candidate_leads",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        SMTPEmailProvider,
        "send",
        lambda *args, **kwargs: pytest.fail(
            "Automation evaluation must not send email"
        ),
    )

    db = SimpleNamespace(
        commit=lambda: None,
        rollback=lambda: None,
    )

    result = evaluate_rule(
        db=db,
        rule_id=31,
        batch_size=50,
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    assert result["evaluated"] == 0


def test_sales_can_mark_reply_but_cannot_create_rule(monkeypatch):
    from app.communications.automation_schemas import (
        AutomationRuleCreate,
        LeadAutomationStateUpdate,
    )
    from app.routers import communication_automation

    events = []

    monkeypatch.setattr(
        communication_automation.automation_repository,
        "upsert_lead_automation_state",
        lambda **kwargs: events.append(kwargs) or {
            "lead_id": kwargs["lead_id"],
            "reply_received_at": datetime.now(timezone.utc),
            "automation_paused": False,
            "pause_reason": None,
            "updated_by": kwargs["user_id"],
            "updated_at": datetime.now(timezone.utc),
        },
    )

    state = communication_automation.update_lead_automation_state(
        lead_id=41,
        payload=LeadAutomationStateUpdate(
            reply_received=True,
        ),
        db=object(),
        user=user("sales"),
    )

    assert state["lead_id"] == 41

    with pytest.raises(HTTPException) as error:
        communication_automation.create_automation_rule(
            payload=AutomationRuleCreate(
                name="Seven-day follow-up",
                template_id=10,
                sender_identity_id=11,
                inactivity_days=7,
            ),
            db=object(),
            user=user("sales"),
        )

    assert error.value.status_code == 403


def test_internal_automation_run_requires_worker_token():
    from app.routers import communication_automation

    with pytest.raises(HTTPException) as error:
        communication_automation.run_automation_rules(
            payload=SimpleNamespace(
                rule_id=None,
                batch_size=50,
            ),
            x_worker_token="wrong-token",
            db=object(),
        )

    assert error.value.status_code == 401


def test_main_mounts_automation_router():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert (
        "from app.routers import communication_automation "
        "as communication_automation_router"
    ) in source
    assert (
        "app.include_router("
        "communication_automation_router.router"
        ")"
    ) in source
