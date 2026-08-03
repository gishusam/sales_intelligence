"""Conservative inactivity automation that creates drafts for review."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.communications import automation_repository as repository
from app.communications.automation_schemas import (
    AutomationRuleCreate,
    AutomationRuleUpdate,
)
from app.communications.templates import render_template


_CLOSED_STATUSES = {
    "won",
    "lost",
    "closed",
    "converted",
    "do_not_contact",
}


class AutomationError(ValueError):
    pass


class AutomationNotFoundError(AutomationError):
    pass


class AutomationValidationError(AutomationError):
    pass


class AutomationConflictError(AutomationError):
    pass


def _validate_dependencies(
    *,
    db: Session,
    template_id: int,
    sender_identity_id: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    template = repository.get_template(
        db=db,
        template_id=template_id,
    )

    if template is None:
        raise AutomationValidationError(
            "Communication template not found"
        )

    if (
        template.get("template_type") != "followup"
        or not template.get("is_active")
    ):
        raise AutomationValidationError(
            "Automation requires an active follow-up template"
        )

    sender = repository.get_sender_identity(
        db=db,
        sender_identity_id=sender_identity_id,
    )

    if sender is None:
        raise AutomationValidationError(
            "Sender identity not found"
        )

    if not sender.get("is_active"):
        raise AutomationValidationError(
            "Sender identity is inactive"
        )

    return template, sender


def create_rule(
    *,
    db: Session,
    payload: AutomationRuleCreate,
    user: CurrentUser,
) -> dict[str, Any]:
    _validate_dependencies(
        db=db,
        template_id=payload.template_id,
        sender_identity_id=payload.sender_identity_id,
    )

    try:
        return repository.create_rule(
            db=db,
            payload=payload,
            user=user,
        )
    except repository.AutomationConflict as exc:
        raise AutomationConflictError(str(exc)) from exc


def update_rule(
    *,
    db: Session,
    rule_id: int,
    payload: AutomationRuleUpdate,
    user: CurrentUser,
) -> dict[str, Any]:
    current = repository.get_rule(
        db=db,
        rule_id=rule_id,
    )

    if current is None:
        raise AutomationNotFoundError(
            "Automation rule not found"
        )

    template_id = (
        payload.template_id
        if payload.template_id is not None
        else current["template_id"]
    )
    sender_identity_id = (
        payload.sender_identity_id
        if payload.sender_identity_id is not None
        else current["sender_identity_id"]
    )

    _validate_dependencies(
        db=db,
        template_id=template_id,
        sender_identity_id=sender_identity_id,
    )

    updated = repository.update_rule(
        db=db,
        rule_id=rule_id,
        payload=payload,
        user=user,
    )

    if updated is None:
        raise AutomationNotFoundError(
            "Automation rule not found"
        )

    return updated


def _reason_to_skip(
    *,
    db: Session,
    rule: dict[str, Any],
    lead: dict[str, Any],
) -> str | None:
    if lead.get("reply_received_at") is not None:
        return "reply_received"

    if lead.get("automation_paused"):
        return "automation_paused"

    if str(lead.get("status") or "").lower() in _CLOSED_STATUSES:
        return "closed_lead"

    if repository.is_suppressed(
        db=db,
        email_address=lead["email"],
    ):
        return "suppressed"

    if repository.count_rule_drafts_for_lead(
        db=db,
        rule_id=rule["id"],
        lead_id=lead["id"],
    ) >= rule["max_drafts_per_lead"]:
        return "draft_limit_reached"

    return None


def _context(
    lead: dict[str, Any],
    sender: dict[str, Any],
) -> dict[str, str]:
    return {
        "contact_name": (
            lead.get("contact_person")
            or lead.get("owner_name")
            or "Property Manager"
        ),
        "company_name": (
            lead.get("name")
            or "your company"
        ),
        "area": lead.get("area") or "Nairobi",
        "rep_name": sender["display_name"],
        "rep_email": sender["email_address"],
    }


def evaluate_rule(
    *,
    db: Session,
    rule_id: int,
    batch_size: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    rule = repository.get_rule(
        db=db,
        rule_id=rule_id,
    )

    if rule is None:
        raise AutomationNotFoundError(
            "Automation rule not found"
        )

    if not rule.get("is_active"):
        raise AutomationValidationError(
            "Automation rule is inactive"
        )

    template, sender = _validate_dependencies(
        db=db,
        template_id=rule["template_id"],
        sender_identity_id=rule["sender_identity_id"],
    )

    leads = repository.list_candidate_leads(
        db=db,
        inactivity_days=rule["inactivity_days"],
        batch_size=batch_size,
        now=now,
    )

    summary = {
        "rule_id": rule_id,
        "evaluated": len(leads),
        "drafted": 0,
        "skipped": 0,
        "existing": 0,
    }

    try:
        for lead in leads:
            window = now.date().isoformat()
            key = (
                f"automation:{rule_id}:"
                f"lead:{lead['id']}:"
                f"{window}"
            )

            if repository.find_execution_by_key(
                db=db,
                idempotency_key=key,
            ) is not None:
                summary["existing"] += 1
                continue

            reason = _reason_to_skip(
                db=db,
                rule=rule,
                lead=lead,
            )

            if reason is not None:
                repository.record_execution(
                    db=db,
                    rule_id=rule_id,
                    lead_id=lead["id"],
                    email_message_id=None,
                    idempotency_key=key,
                    status="skipped",
                    reason=reason,
                    executed_at=now,
                )
                summary["skipped"] += 1
                continue

            subject, body_text = render_template(
                subject=template["subject"],
                body=template["body_text"],
                values=_context(lead, sender),
            )

            message_id = repository.create_automation_draft(
                db=db,
                automation_rule_id=rule_id,
                lead_id=lead["id"],
                template_id=template["id"],
                sender_identity_id=sender["id"],
                sent_by=rule.get("created_by"),
                recipient_email=lead["email"],
                recipient_name=(
                    lead.get("contact_person")
                    or lead.get("owner_name")
                ),
                subject=subject,
                body_text=body_text,
                body_html=template.get("body_html"),
                idempotency_key=key,
                status="draft",
                message_type="automation",
            )

            if message_id is None:
                summary["existing"] += 1
                continue

            repository.record_execution(
                db=db,
                rule_id=rule_id,
                lead_id=lead["id"],
                email_message_id=message_id,
                idempotency_key=key,
                status="drafted",
                reason=None,
                executed_at=now,
            )
            summary["drafted"] += 1

        db.commit()
        return summary

    except Exception:
        db.rollback()
        raise


def run_active_rules(
    *,
    db: Session,
    rule_id: int | None,
    batch_size: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    rules = repository.list_active_rules(
        db=db,
        rule_id=rule_id,
    )

    results = [
        evaluate_rule(
            db=db,
            rule_id=item["id"],
            batch_size=batch_size,
            now=now,
        )
        for item in rules
    ]

    return {
        "rules_evaluated": len(results),
        "drafted": sum(item["drafted"] for item in results),
        "skipped": sum(item["skipped"] for item in results),
        "existing": sum(item["existing"] for item in results),
        "results": results,
    }
