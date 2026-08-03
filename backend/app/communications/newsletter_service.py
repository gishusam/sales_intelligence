"""Newsletter workflow and test-delivery rules."""

import base64
import hashlib
import hmac
import os
from typing import Any

from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.communications import newsletter_repository as repository
from app.communications.delivery import EmailProvider, OutboundMessage
from app.communications.newsletter_renderer import render_blocks
from app.communications.newsletter_schemas import (
    NewsletterAudienceRequest,
    NewsletterBlock,
    NewsletterCreate,
    NewsletterTestSendRequest,
    NewsletterUpdate,
)
from app.communications.smtp_provider import SMTPEmailProvider


class NewsletterError(ValueError):
    pass


class NewsletterNotFoundError(NewsletterError):
    pass


class NewsletterValidationError(NewsletterError):
    pass


class NewsletterStateError(NewsletterError):
    pass


def get_item(
    *,
    db: Session,
    newsletter_id: int,
) -> dict[str, Any]:
    item = repository.get_newsletter(
        db=db,
        newsletter_id=newsletter_id,
    )

    if item is None:
        raise NewsletterNotFoundError("Newsletter not found")

    return item


def active_sender(
    *,
    db: Session,
    sender_identity_id: int,
) -> dict[str, Any]:
    item = repository.get_sender_identity(
        db=db,
        sender_identity_id=sender_identity_id,
    )

    if item is None:
        raise NewsletterValidationError(
            "Sender identity not found"
        )

    if not item.get("is_active"):
        raise NewsletterValidationError(
            "Sender identity is inactive"
        )

    return item


def create_newsletter(
    *,
    db: Session,
    payload: NewsletterCreate,
    user: CurrentUser,
) -> dict[str, Any]:
    active_sender(
        db=db,
        sender_identity_id=payload.sender_identity_id,
    )
    body_text, body_html = render_blocks(payload.blocks)

    return repository.create_newsletter(
        db=db,
        values={
            "name": payload.name,
            "subject": payload.subject,
            "preview_text": payload.preview_text,
            "sender_identity_id": payload.sender_identity_id,
            "blocks": [
                item.model_dump()
                for item in payload.blocks
            ],
            "body_text": body_text,
            "body_html": body_html,
        },
        user=user,
    )


def update_newsletter(
    *,
    db: Session,
    newsletter_id: int,
    payload: NewsletterUpdate,
    user: CurrentUser,
) -> dict[str, Any]:
    current = get_item(
        db=db,
        newsletter_id=newsletter_id,
    )

    if current["status"] != "draft":
        raise NewsletterStateError(
            "Only draft newsletters can be edited"
        )

    changes = payload.model_dump(exclude_unset=True)
    sender_identity_id = changes.get(
        "sender_identity_id",
        current["sender_identity_id"],
    )
    active_sender(
        db=db,
        sender_identity_id=sender_identity_id,
    )

    blocks = (
        payload.blocks
        if payload.blocks is not None
        else [
            NewsletterBlock(**item)
            for item in current["blocks"]
        ]
    )
    body_text, body_html = render_blocks(blocks)

    return repository.update_newsletter(
        db=db,
        newsletter_id=newsletter_id,
        values={
            "name": changes.get("name", current["name"]),
            "subject": changes.get(
                "subject",
                current["subject"],
            ),
            "preview_text": changes.get(
                "preview_text",
                current["preview_text"],
            ),
            "sender_identity_id": sender_identity_id,
            "blocks": [item.model_dump() for item in blocks],
            "body_text": body_text,
            "body_html": body_html,
        },
        user=user,
    ) or {}


def submit_for_review(
    *,
    db: Session,
    newsletter_id: int,
    user: CurrentUser,
) -> dict[str, Any]:
    item = get_item(
        db=db,
        newsletter_id=newsletter_id,
    )

    if item["status"] != "draft":
        raise NewsletterStateError(
            "Only draft newsletters can enter review"
        )

    repository.mark_in_review(
        db=db,
        newsletter_id=newsletter_id,
        user_id=user.id,
    )
    db.commit()

    return {
        "newsletter_id": newsletter_id,
        "status": "in_review",
    }


def approve_newsletter(
    *,
    db: Session,
    newsletter_id: int,
    user: CurrentUser,
) -> dict[str, Any]:
    item = get_item(
        db=db,
        newsletter_id=newsletter_id,
    )

    if item["status"] != "in_review":
        raise NewsletterStateError(
            "Only newsletters in review can be approved"
        )

    active_sender(
        db=db,
        sender_identity_id=item["sender_identity_id"],
    )
    repository.mark_approved(
        db=db,
        newsletter_id=newsletter_id,
        user_id=user.id,
    )
    db.commit()

    return {
        "newsletter_id": newsletter_id,
        "status": "approved",
    }


def snapshot_audience(
    *,
    db: Session,
    newsletter_id: int,
    payload: NewsletterAudienceRequest,
    user: CurrentUser,
) -> dict[str, Any]:
    item = get_item(
        db=db,
        newsletter_id=newsletter_id,
    )

    if item["status"] not in {"draft", "in_review"}:
        raise NewsletterStateError(
            "Audience can only be changed before approval"
        )

    unique_ids = list(dict.fromkeys(payload.lead_ids))
    leads = repository.get_leads_by_ids(
        db=db,
        lead_ids=unique_ids,
    )
    result = {
        "requested": len(payload.lead_ids),
        "unique_requested": len(unique_ids),
        "added": 0,
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
                    {"lead_id": lead_id, "status": "missing_lead"}
                )
                continue

            email = (lead.get("email") or "").strip().lower()

            if not email:
                result["missing_email"] += 1
                result["results"].append(
                    {"lead_id": lead_id, "status": "missing_email"}
                )
                continue

            if repository.is_suppressed(
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

            if repository.recipient_exists(
                db=db,
                newsletter_id=newsletter_id,
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

            recipient_id = repository.create_recipient(
                db=db,
                newsletter_id=newsletter_id,
                lead_id=lead_id,
                recipient_email=email,
                recipient_name=(
                    lead.get("contact_person")
                    or lead.get("owner_name")
                ),
                company_name=lead.get("name"),
                added_by=user.id,
            )
            result["added"] += 1
            result["results"].append(
                {
                    "lead_id": lead_id,
                    "email": email,
                    "recipient_id": recipient_id,
                    "status": "added",
                }
            )

        db.commit()
        return result

    except Exception:
        db.rollback()
        raise


def create_unsubscribe_token(
    email_address: str,
    *,
    secret: str,
) -> str:
    normalized = email_address.strip().lower()
    payload = base64.urlsafe_b64encode(
        normalized.encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        secret.encode("utf-8"),
        payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()

    return f"{payload}.{signature}"


def decode_unsubscribe_token(
    token: str,
    *,
    secret: str,
) -> str:
    try:
        payload, signature = token.split(".", 1)
    except ValueError as exc:
        raise NewsletterValidationError(
            "Invalid unsubscribe token"
        ) from exc

    expected = hmac.new(
        secret.encode("utf-8"),
        payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        raise NewsletterValidationError(
            "Invalid unsubscribe token"
        )

    padding = "=" * (-len(payload) % 4)

    try:
        return base64.urlsafe_b64decode(
            payload + padding
        ).decode("utf-8").strip().lower()
    except Exception as exc:
        raise NewsletterValidationError(
            "Invalid unsubscribe token"
        ) from exc


def test_send(
    *,
    db: Session,
    newsletter_id: int,
    payload: NewsletterTestSendRequest,
    user: CurrentUser,
    provider: EmailProvider | None = None,
) -> dict[str, Any]:
    item = get_item(
        db=db,
        newsletter_id=newsletter_id,
    )
    sender = active_sender(
        db=db,
        sender_identity_id=item["sender_identity_id"],
    )
    secret = os.getenv(
        "NEWSLETTER_UNSUBSCRIBE_SECRET",
        os.getenv("SECRET_KEY", "development-secret"),
    )
    token = create_unsubscribe_token(
        payload.to_email,
        secret=secret,
    )
    base_url = os.getenv(
        "PUBLIC_API_BASE_URL",
        "http://localhost:8000",
    ).rstrip("/")
    unsubscribe_url = (
        f"{base_url}/api/communications/newsletters/"
        f"unsubscribe/{token}"
    )
    body_text = item["body_text"].replace(
        "{unsubscribe_link}",
        unsubscribe_url,
    )
    body_html = item["body_html"].replace(
        "{unsubscribe_link}",
        unsubscribe_url,
    )
    message_id = repository.create_test_message(
        db=db,
        newsletter_id=newsletter_id,
        sender_identity_id=sender["id"],
        sent_by=user.id,
        recipient_email=payload.to_email,
        subject=item["subject"],
        body_text=body_text,
        body_html=body_html,
    )
    db.commit()

    try:
        result = (provider or SMTPEmailProvider()).send(
            OutboundMessage(
                from_name=sender["display_name"],
                from_email=sender["email_address"],
                reply_to=sender.get("reply_to_address"),
                to_email=payload.to_email,
                subject=item["subject"],
                body_text=body_text,
                body_html=body_html,
            )
        )
    except Exception as exc:
        error = str(exc) or "Newsletter test delivery failed"
        repository.mark_test_failed(
            db=db,
            message_id=message_id,
            error_message=error,
        )
        db.commit()
        raise NewsletterValidationError(error) from exc

    if not result.accepted:
        error = (
            result.error_message
            or "Newsletter test delivery failed"
        )
        repository.mark_test_failed(
            db=db,
            message_id=message_id,
            error_message=error,
        )
        db.commit()
        raise NewsletterValidationError(error)

    repository.mark_test_sent(
        db=db,
        message_id=message_id,
        provider_message_id=result.provider_message_id,
    )
    db.commit()

    return {
        "message_id": message_id,
        "status": "sent",
        "provider_message_id": result.provider_message_id,
        "error_message": None,
    }


def unsubscribe(
    *,
    db: Session,
    token: str,
) -> dict[str, str]:
    secret = os.getenv(
        "NEWSLETTER_UNSUBSCRIBE_SECRET",
        os.getenv("SECRET_KEY", "development-secret"),
    )
    email = decode_unsubscribe_token(
        token,
        secret=secret,
    )
    repository.suppress_email(
        db=db,
        email_address=email,
        reason="Newsletter unsubscribe",
    )
    db.commit()

    return {
        "email_address": email,
        "status": "unsubscribed",
    }
