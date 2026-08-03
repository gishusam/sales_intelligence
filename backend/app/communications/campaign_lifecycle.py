"""Campaign preparation, scheduling, pause, resume, and cancel rules."""

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.communications import campaign_execution_repository as repository
from app.communications.campaign_lifecycle_schemas import (
    CampaignScheduleRequest,
)
from app.communications.templates import render_template


_ALLOWED_TRANSITIONS = {
    "draft": {"ready", "cancelled"},
    "ready": {"scheduled", "cancelled"},
    "scheduled": {"running", "paused", "cancelled"},
    "running": {"paused", "completed", "cancelled"},
    "paused": {"scheduled", "running", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


class CampaignLifecycleError(ValueError):
    """Base client-facing lifecycle error."""


class CampaignNotFoundError(CampaignLifecycleError):
    pass


class CampaignStateError(CampaignLifecycleError):
    pass


class CampaignPreflightError(CampaignLifecycleError):
    pass


def validate_transition(
    current_status: str,
    target_status: str,
) -> None:
    if target_status not in _ALLOWED_TRANSITIONS.get(
        current_status,
        set(),
    ):
        raise CampaignStateError(
            "Invalid campaign transition: "
            f"{current_status} -> {target_status}"
        )


def _campaign(
    *,
    db: Session,
    campaign_id: int,
) -> dict[str, Any]:
    item = repository.get_campaign(
        db=db,
        campaign_id=campaign_id,
    )

    if item is None:
        raise CampaignNotFoundError(
            "Campaign not found"
        )

    return item


def _active_sender(
    *,
    db: Session,
    sender_identity_id: int,
) -> dict[str, Any]:
    sender = repository.get_sender_identity(
        db=db,
        sender_identity_id=sender_identity_id,
    )

    if sender is None:
        raise CampaignPreflightError(
            "Sender identity not found"
        )

    if not sender.get("is_active"):
        raise CampaignPreflightError(
            "Sender identity is inactive"
        )

    return sender


def _validate_steps(
    *,
    campaign: dict[str, Any],
    steps: list[dict[str, Any]],
) -> None:
    if not steps:
        raise CampaignPreflightError(
            "Campaign requires at least one step"
        )

    expected = list(range(1, len(steps) + 1))
    actual = [step["step_order"] for step in steps]

    if actual != expected:
        raise CampaignPreflightError(
            "Campaign step order must be contiguous "
            "and start at 1"
        )

    for step in steps:
        if not step.get("template_is_active"):
            raise CampaignPreflightError(
                "Every campaign template must be active"
            )

        if (
            step.get("template_type")
            != campaign.get("campaign_type")
        ):
            raise CampaignPreflightError(
                "Every template type must match "
                "the campaign type"
            )


def prepare_campaign(
    *,
    db: Session,
    campaign_id: int,
    user: CurrentUser,
) -> dict[str, Any]:
    campaign = _campaign(
        db=db,
        campaign_id=campaign_id,
    )
    validate_transition(
        campaign["status"],
        "ready",
    )
    _active_sender(
        db=db,
        sender_identity_id=campaign["sender_identity_id"],
    )

    steps = repository.list_steps_for_preflight(
        db=db,
        campaign_id=campaign_id,
    )
    _validate_steps(
        campaign=campaign,
        steps=steps,
    )

    recipients = repository.list_enrolled_recipients(
        db=db,
        campaign_id=campaign_id,
    )

    if not recipients:
        raise CampaignPreflightError(
            "Campaign requires at least one enrolled recipient"
        )

    try:
        for step in steps:
            repository.freeze_step_snapshot(
                db=db,
                step_id=step["id"],
                subject=(
                    step.get("subject_override")
                    or step["template_subject"]
                ),
                body_text=(
                    step.get("body_text_override")
                    or step["template_body_text"]
                ),
                body_html=step.get("template_body_html"),
                template_version=step["template_version"],
            )

        repository.freeze_recipient_personalization(
            db=db,
            campaign_id=campaign_id,
        )

        eligible = 0
        suppressed = 0

        for recipient in recipients:
            if repository.is_suppressed(
                db=db,
                email_address=recipient["recipient_email"],
            ):
                repository.mark_recipient_suppressed(
                    db=db,
                    recipient_id=recipient["id"],
                    reason=(
                        "Suppressed during campaign preparation"
                    ),
                )
                suppressed += 1
            else:
                eligible += 1

        if eligible == 0:
            raise CampaignPreflightError(
                "No eligible recipients remain "
                "after suppression checks"
            )

        repository.mark_campaign_ready(
            db=db,
            campaign_id=campaign_id,
            updated_by=user.id,
        )
        db.commit()

        return {
            "campaign_id": campaign_id,
            "status": "ready",
            "step_count": len(steps),
            "eligible_recipients": eligible,
            "suppressed_recipients": suppressed,
        }

    except Exception:
        db.rollback()
        raise


def _personalization(
    recipient: dict[str, Any],
) -> dict[str, str]:
    return {
        "contact_name": (
            recipient.get("recipient_name")
            or "Property Manager"
        ),
        "company_name": (
            recipient.get("company_name_snapshot")
            or "your company"
        ),
        "area": (
            recipient.get("area_snapshot")
            or "Nairobi"
        ),
        "rep_name": (
            recipient.get("rep_name_snapshot")
            or "Nyumba Zetu Sales"
        ),
        "rep_email": (
            recipient.get("rep_email_snapshot")
            or ""
        ),
    }


def schedule_campaign(
    *,
    db: Session,
    campaign_id: int,
    payload: CampaignScheduleRequest,
    user: CurrentUser,
) -> dict[str, Any]:
    campaign = _campaign(
        db=db,
        campaign_id=campaign_id,
    )
    validate_transition(
        campaign["status"],
        "scheduled",
    )
    _active_sender(
        db=db,
        sender_identity_id=campaign["sender_identity_id"],
    )

    step = repository.get_first_frozen_step(
        db=db,
        campaign_id=campaign_id,
    )

    if step is None:
        raise CampaignPreflightError(
            "Campaign must be prepared before scheduling"
        )

    recipients = repository.list_eligible_recipients(
        db=db,
        campaign_id=campaign_id,
    )

    if not recipients:
        raise CampaignPreflightError(
            "Campaign has no eligible recipients"
        )

    queued = 0
    existing = 0
    newly_suppressed = 0

    try:
        for recipient in recipients:
            email = recipient["recipient_email"]

            if repository.is_suppressed(
                db=db,
                email_address=email,
            ):
                repository.mark_recipient_suppressed(
                    db=db,
                    recipient_id=recipient["id"],
                    reason=(
                        "Suppressed during campaign scheduling"
                    ),
                )
                newly_suppressed += 1
                continue

            subject, body_text = render_template(
                subject=step["snapshot_subject"],
                body=step["snapshot_body_text"],
                values=_personalization(recipient),
            )

            due_at = (
                payload.scheduled_for
                + timedelta(days=step["delay_days"])
            )
            key = (
                f"campaign:{campaign_id}:"
                f"recipient:{recipient['id']}:"
                f"step:{step['id']}"
            )

            created = repository.queue_campaign_message(
                db=db,
                campaign_id=campaign_id,
                campaign_recipient_id=recipient["id"],
                campaign_step_id=step["id"],
                lead_id=recipient.get("lead_id"),
                template_id=step["template_id"],
                sender_identity_id=(
                    campaign["sender_identity_id"]
                ),
                sent_by=campaign.get("created_by"),
                recipient_email=email,
                recipient_name=recipient.get(
                    "recipient_name"
                ),
                subject=subject,
                body_text=body_text,
                body_html=step.get(
                    "snapshot_body_html"
                ),
                idempotency_key=key,
                scheduled_for=due_at,
            )

            if created:
                queued += 1
                repository.mark_recipient_queued(
                    db=db,
                    recipient_id=recipient["id"],
                    step_order=step["step_order"],
                    next_run_at=due_at,
                )
            else:
                existing += 1

        if queued + existing == 0:
            raise CampaignPreflightError(
                "No recipient messages could be queued"
            )

        repository.mark_campaign_scheduled(
            db=db,
            campaign_id=campaign_id,
            scheduled_for=payload.scheduled_for,
            updated_by=user.id,
        )
        db.commit()

        return {
            "campaign_id": campaign_id,
            "status": "scheduled",
            "scheduled_for": payload.scheduled_for,
            "queued_messages": queued,
            "existing_messages": existing,
            "newly_suppressed": newly_suppressed,
        }

    except Exception:
        db.rollback()
        raise


def pause_campaign(
    *,
    db: Session,
    campaign_id: int,
    user: CurrentUser,
) -> dict[str, Any]:
    campaign = _campaign(
        db=db,
        campaign_id=campaign_id,
    )
    validate_transition(
        campaign["status"],
        "paused",
    )

    repository.mark_campaign_paused(
        db=db,
        campaign_id=campaign_id,
        previous_status=campaign["status"],
        updated_by=user.id,
    )
    db.commit()

    return {
        "campaign_id": campaign_id,
        "status": "paused",
    }


def resume_campaign(
    *,
    db: Session,
    campaign_id: int,
    user: CurrentUser,
) -> dict[str, Any]:
    campaign = _campaign(
        db=db,
        campaign_id=campaign_id,
    )

    if campaign["status"] != "paused":
        raise CampaignStateError(
            "Only paused campaigns can be resumed"
        )

    target = (
        campaign.get("paused_from_status")
        or (
            "running"
            if campaign.get("started_at")
            else "scheduled"
        )
    )
    validate_transition("paused", target)

    repository.mark_campaign_resumed(
        db=db,
        campaign_id=campaign_id,
        target_status=target,
        updated_by=user.id,
    )
    db.commit()

    return {
        "campaign_id": campaign_id,
        "status": target,
    }


def cancel_campaign(
    *,
    db: Session,
    campaign_id: int,
    user: CurrentUser,
) -> dict[str, Any]:
    campaign = _campaign(
        db=db,
        campaign_id=campaign_id,
    )
    validate_transition(
        campaign["status"],
        "cancelled",
    )

    repository.cancel_campaign_and_pending_jobs(
        db=db,
        campaign_id=campaign_id,
        updated_by=user.id,
    )
    db.commit()

    return {
        "campaign_id": campaign_id,
        "status": "cancelled",
    }
