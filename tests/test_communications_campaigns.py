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


def current_user(role="admin"):
    return CurrentUser(
        id=3,
        name="Admin User",
        email="admin@example.test",
        role=role,
    )


def campaign_record(**overrides):
    values = {
        "id": 21,
        "name": "Kilimani agency outreach",
        "description": "Draft campaign",
        "campaign_type": "cold",
        "sender_identity_id": 11,
        "status": "draft",
        "created_by": 3,
        "updated_by": 3,
        "created_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return values


def sender_record(**overrides):
    values = {
        "id": 11,
        "display_name": "Nyumba Zetu Sales",
        "email_address": "sales@nyumbazetu.example",
        "reply_to_address": None,
        "provider": "smtp",
        "is_active": True,
    }
    values.update(overrides)
    return values


def template_record(**overrides):
    values = {
        "id": 9,
        "template_type": "cold",
        "subject": "Hello {company_name}",
        "body_text": (
            "Hi {contact_name},\n"
            "Regards,\n{rep_name}\n{rep_email}"
        ),
        "is_active": True,
    }
    values.update(overrides)
    return values


def test_campaign_create_is_always_draft():
    from app.communications.campaign_schemas import CampaignCreate

    campaign = CampaignCreate(
        name="Kilimani agency outreach",
        campaign_type="cold",
        sender_identity_id=11,
    )

    assert campaign.name == "Kilimani agency outreach"

    with pytest.raises(ValidationError):
        CampaignCreate(
            name="Kilimani agency outreach",
            campaign_type="cold",
            sender_identity_id=11,
            status="active",
        )


def test_campaign_step_requires_positive_order():
    from app.communications.campaign_schemas import CampaignStepCreate

    with pytest.raises(ValidationError):
        CampaignStepCreate(
            step_order=0,
            delay_days=0,
            template_id=9,
        )


def test_campaign_update_requires_at_least_one_field():
    from app.communications.campaign_schemas import CampaignUpdate

    with pytest.raises(
        ValidationError,
        match=r"At least one campaign field must be supplied",
    ):
        CampaignUpdate()


def test_create_campaign_rejects_inactive_sender(monkeypatch):
    from app.communications import campaign_repository
    from app.communications.campaign_schemas import CampaignCreate
    from app.communications.campaign_service import (
        CampaignValidationError,
        create_campaign,
    )

    monkeypatch.setattr(
        campaign_repository,
        "get_sender_identity",
        lambda **kwargs: sender_record(is_active=False),
    )

    with pytest.raises(
        CampaignValidationError,
        match=r"Sender identity is inactive",
    ):
        create_campaign(
            db=object(),
            payload=CampaignCreate(
                name="Kilimani agency outreach",
                campaign_type="cold",
                sender_identity_id=11,
            ),
            user=current_user(),
        )


def test_step_template_type_must_match_campaign(monkeypatch):
    from app.communications import campaign_repository
    from app.communications.campaign_schemas import CampaignStepCreate
    from app.communications.campaign_service import (
        CampaignValidationError,
        add_campaign_step,
    )

    monkeypatch.setattr(
        campaign_repository,
        "get_campaign",
        lambda **kwargs: campaign_record(campaign_type="cold"),
    )
    monkeypatch.setattr(
        campaign_repository,
        "get_template",
        lambda **kwargs: template_record(
            template_type="newsletter",
        ),
    )
    monkeypatch.setattr(
        campaign_repository,
        "step_order_exists",
        lambda **kwargs: False,
    )

    with pytest.raises(
        CampaignValidationError,
        match=r"Template type must match campaign type",
    ):
        add_campaign_step(
            db=object(),
            campaign_id=21,
            payload=CampaignStepCreate(
                step_order=1,
                delay_days=0,
                template_id=9,
            ),
            user=current_user(),
        )


def test_non_draft_campaign_cannot_be_modified(monkeypatch):
    from app.communications import campaign_repository
    from app.communications.campaign_schemas import CampaignUpdate
    from app.communications.campaign_service import (
        CampaignStateError,
        update_campaign,
    )

    monkeypatch.setattr(
        campaign_repository,
        "get_campaign",
        lambda **kwargs: campaign_record(status="active"),
    )

    with pytest.raises(
        CampaignStateError,
        match=r"Only draft campaigns can be modified",
    ):
        update_campaign(
            db=object(),
            campaign_id=21,
            payload=CampaignUpdate(name="Renamed"),
            user=current_user(),
        )


def test_enrolment_filters_missing_suppressed_and_duplicate_leads(
    monkeypatch,
):
    from app.communications import campaign_repository
    from app.communications.campaign_schemas import (
        CampaignEnrollmentRequest,
    )
    from app.communications.campaign_service import enroll_campaign_leads

    leads = {
        1: {
            "id": 1,
            "name": "Alpha Properties",
            "contact_person": "Alice",
            "owner_name": None,
            "email": "alice@example.test",
        },
        2: {
            "id": 2,
            "name": "No Email Properties",
            "contact_person": "No Email",
            "owner_name": None,
            "email": None,
        },
        3: {
            "id": 3,
            "name": "Suppressed Properties",
            "contact_person": "Suppressed",
            "owner_name": None,
            "email": "blocked@example.test",
        },
        4: {
            "id": 4,
            "name": "Duplicate Properties",
            "contact_person": "Duplicate",
            "owner_name": None,
            "email": "duplicate@example.test",
        },
    }

    enrolled = []
    commits = []

    monkeypatch.setattr(
        campaign_repository,
        "get_campaign",
        lambda **kwargs: campaign_record(),
    )
    monkeypatch.setattr(
        campaign_repository,
        "get_leads_by_ids",
        lambda **kwargs: leads,
    )
    monkeypatch.setattr(
        campaign_repository,
        "is_recipient_suppressed",
        lambda **kwargs: (
            kwargs["email_address"] == "blocked@example.test"
        ),
    )
    monkeypatch.setattr(
        campaign_repository,
        "recipient_exists",
        lambda **kwargs: kwargs["lead_id"] == 4,
    )
    monkeypatch.setattr(
        campaign_repository,
        "enroll_recipient",
        lambda **kwargs: enrolled.append(kwargs),
    )

    db = SimpleNamespace(
        commit=lambda: commits.append(True),
        rollback=lambda: None,
    )

    result = enroll_campaign_leads(
        db=db,
        campaign_id=21,
        payload=CampaignEnrollmentRequest(
            lead_ids=[1, 1, 2, 3, 4, 999],
        ),
        user=current_user(),
    )

    assert result["requested"] == 6
    assert result["unique_requested"] == 5
    assert result["enrolled"] == 1
    assert result["missing_email"] == 1
    assert result["suppressed"] == 1
    assert result["duplicates"] == 1
    assert result["missing_leads"] == 1
    assert len(enrolled) == 1
    assert enrolled[0]["lead_id"] == 1
    assert enrolled[0]["recipient_email"] == "alice@example.test"
    assert commits == [True]


def test_enrolment_never_sends_email(monkeypatch):
    from app.communications import campaign_repository
    from app.communications.campaign_schemas import (
        CampaignEnrollmentRequest,
    )
    from app.communications.campaign_service import enroll_campaign_leads
    from app.communications.smtp_provider import SMTPEmailProvider

    monkeypatch.setattr(
        campaign_repository,
        "get_campaign",
        lambda **kwargs: campaign_record(),
    )
    monkeypatch.setattr(
        campaign_repository,
        "get_leads_by_ids",
        lambda **kwargs: {
            1: {
                "id": 1,
                "name": "Alpha Properties",
                "contact_person": "Alice",
                "owner_name": None,
                "email": "alice@example.test",
            }
        },
    )
    monkeypatch.setattr(
        campaign_repository,
        "is_recipient_suppressed",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        campaign_repository,
        "recipient_exists",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        campaign_repository,
        "enroll_recipient",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        SMTPEmailProvider,
        "send",
        lambda *args, **kwargs: pytest.fail(
            "Campaign enrolment must never send email"
        ),
    )

    db = SimpleNamespace(
        commit=lambda: None,
        rollback=lambda: None,
    )

    result = enroll_campaign_leads(
        db=db,
        campaign_id=21,
        payload=CampaignEnrollmentRequest(lead_ids=[1]),
        user=current_user(),
    )

    assert result["enrolled"] == 1


def test_sales_can_read_but_cannot_create_campaign(monkeypatch):
    from app.communications.campaign_schemas import CampaignCreate
    from app.routers import communication_campaigns

    monkeypatch.setattr(
        communication_campaigns.campaign_repository,
        "list_campaigns",
        lambda **kwargs: [campaign_record()],
    )

    rows = communication_campaigns.list_campaigns(
        status_filter=None,
        db=object(),
        user=current_user("sales"),
    )

    assert rows[0]["id"] == 21

    called = False

    def fake_create(**kwargs):
        nonlocal called
        called = True
        return campaign_record()

    monkeypatch.setattr(
        communication_campaigns.campaign_service,
        "create_campaign",
        fake_create,
    )

    with pytest.raises(HTTPException) as error:
        communication_campaigns.create_campaign(
            payload=CampaignCreate(
                name="Kilimani agency outreach",
                campaign_type="cold",
                sender_identity_id=11,
            ),
            db=object(),
            user=current_user("sales"),
        )

    assert error.value.status_code == 403
    assert called is False


def test_main_mounts_campaign_router():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert (
        "from app.routers import communication_campaigns "
        "as communication_campaigns_router"
    ) in source
    assert (
        "app.include_router(communication_campaigns_router.router)"
        in source
    )
