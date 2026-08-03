"""Business rules for safe draft campaigns and lead enrolment."""

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.communications import campaign_repository
from app.communications.campaign_schemas import (
    CampaignCreate,
    CampaignEnrollmentRequest,
    CampaignStepCreate,
    CampaignUpdate,
)


class CampaignError(ValueError):
    """Base client-facing campaign error."""


class CampaignNotFoundError(CampaignError):
    pass


class CampaignValidationError(CampaignError):
    pass


class CampaignStateError(CampaignError):
    pass


class CampaignConflictError(CampaignError):
    pass


def _campaign(
    *,
    db: Session,
    campaign_id: int,
) -> dict[str, Any]:
    campaign = campaign_repository.get_campaign(
        db=db,
        campaign_id=campaign_id,
    )

    if campaign is None:
        raise CampaignNotFoundError(
            "Campaign not found"
        )

    return campaign


def _require_draft(campaign: dict[str, Any]) -> None:
    if campaign["status"] != "draft":
        raise CampaignStateError(
            "Only draft campaigns can be modified"
        )


def _active_sender(
    *,
    db: Session,
    sender_identity_id: int,
) -> dict[str, Any]:
    sender = campaign_repository.get_sender_identity(
        db=db,
        sender_identity_id=sender_identity_id,
    )

    if sender is None:
        raise CampaignValidationError(
            "Sender identity not found"
        )

    if not sender.get("is_active"):
        raise CampaignValidationError(
            "Sender identity is inactive"
        )

    return sender


def create_campaign(
    *,
    db: Session,
    payload: CampaignCreate,
    user: CurrentUser,
) -> dict[str, Any]:
    _active_sender(
        db=db,
        sender_identity_id=payload.sender_identity_id,
    )

    try:
        return campaign_repository.create_campaign(
            db=db,
            payload=payload,
            user=user,
        )
    except campaign_repository.CampaignConflict as exc:
        raise CampaignConflictError(str(exc)) from exc


def update_campaign(
    *,
    db: Session,
    campaign_id: int,
    payload: CampaignUpdate,
    user: CurrentUser,
) -> dict[str, Any]:
    campaign = _campaign(
        db=db,
        campaign_id=campaign_id,
    )
    _require_draft(campaign)

    if payload.sender_identity_id is not None:
        _active_sender(
            db=db,
            sender_identity_id=(
                payload.sender_identity_id
            ),
        )

    updated = campaign_repository.update_campaign(
        db=db,
        campaign_id=campaign_id,
        payload=payload,
        user=user,
    )

    if updated is None:
        raise CampaignNotFoundError(
            "Campaign not found"
        )

    return updated


def add_campaign_step(
    *,
    db: Session,
    campaign_id: int,
    payload: CampaignStepCreate,
    user: CurrentUser,
) -> dict[str, Any]:
    campaign = _campaign(
        db=db,
        campaign_id=campaign_id,
    )
    _require_draft(campaign)

    template = campaign_repository.get_template(
        db=db,
        template_id=payload.template_id,
    )

    if template is None:
        raise CampaignValidationError(
            "Communication template not found"
        )

    if not template.get("is_active"):
        raise CampaignValidationError(
            "Communication template is inactive"
        )

    if (
        template.get("template_type")
        != campaign.get("campaign_type")
    ):
        raise CampaignValidationError(
            "Template type must match campaign type"
        )

    if campaign_repository.step_order_exists(
        db=db,
        campaign_id=campaign_id,
        step_order=payload.step_order,
    ):
        raise CampaignConflictError(
            "Campaign step order already exists"
        )

    try:
        return campaign_repository.add_campaign_step(
            db=db,
            campaign_id=campaign_id,
            payload=payload,
        )
    except campaign_repository.CampaignConflict as exc:
        raise CampaignConflictError(str(exc)) from exc


def enroll_campaign_leads(
    *,
    db: Session,
    campaign_id: int,
    payload: CampaignEnrollmentRequest,
    user: CurrentUser,
) -> dict[str, Any]:
    campaign = _campaign(
        db=db,
        campaign_id=campaign_id,
    )
    _require_draft(campaign)

    unique_ids = list(dict.fromkeys(payload.lead_ids))
    leads = campaign_repository.get_leads_by_ids(
        db=db,
        lead_ids=unique_ids,
    )

    result: dict[str, Any] = {
        "requested": len(payload.lead_ids),
        "unique_requested": len(unique_ids),
        "enrolled": 0,
        "suppressed": 0,
        "duplicates": 0,
        "missing_email": 0,
        "missing_leads": 0,
        "results": [],
    }

    try:
        for lead_id in unique_ids:
            lead = leads.get(lead_id)

            if lead is None:
                result["missing_leads"] += 1
                result["results"].append(
                    {
                        "lead_id": lead_id,
                        "status": "missing_lead",
                    }
                )
                continue

            email = (
                lead.get("email") or ""
            ).strip().lower()

            if not email:
                result["missing_email"] += 1
                result["results"].append(
                    {
                        "lead_id": lead_id,
                        "status": "missing_email",
                    }
                )
                continue

            if campaign_repository.is_recipient_suppressed(
                db=db,
                email_address=email,
            ):
                result["suppressed"] += 1
                result["results"].append(
                    {
                        "lead_id": lead_id,
                        "email": email,
                        "status": "suppressed",
                    }
                )
                continue

            if campaign_repository.recipient_exists(
                db=db,
                campaign_id=campaign_id,
                lead_id=lead_id,
                email_address=email,
            ):
                result["duplicates"] += 1
                result["results"].append(
                    {
                        "lead_id": lead_id,
                        "email": email,
                        "status": "duplicate",
                    }
                )
                continue

            recipient_name = (
                lead.get("contact_person")
                or lead.get("owner_name")
                or lead.get("name")
            )

            recipient_id = (
                campaign_repository.enroll_recipient(
                    db=db,
                    campaign_id=campaign_id,
                    lead_id=lead_id,
                    recipient_email=email,
                    recipient_name=recipient_name,
                    enrolled_by=user.id,
                )
            )

            result["enrolled"] += 1
            result["results"].append(
                {
                    "lead_id": lead_id,
                    "email": email,
                    "recipient_id": recipient_id,
                    "status": "enrolled",
                }
            )

        db.commit()
        return result

    except IntegrityError as exc:
        db.rollback()
        raise CampaignConflictError(
            "A recipient was enrolled concurrently"
        ) from exc
