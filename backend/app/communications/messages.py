"""Pure preview and auditable manual email delivery."""

import base64
import binascii
import os

from app.communications import message_repository
from app.communications.delivery import OutboundAttachment, OutboundMessage
from app.communications.message_schemas import MessageSendRequest
from app.communications.smtp_provider import SMTPEmailProvider
from app.communications.templates import render_template


MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


class MessageValidationError(ValueError):
    pass


class MessageResourceNotFoundError(MessageValidationError):
    pass


class RecipientSuppressedError(MessageValidationError):
    pass


class RecipientNotAllowedError(MessageValidationError):
    pass


class SenderUnavailableError(MessageValidationError):
    pass


def context_for(lead, user):
    return {
        "contact_name": (
            lead.get("contact_person")
            or lead.get("owner_name")
            or "Property Manager"
        ),
        "company_name": lead.get("name") or "your company",
        "area": lead.get("area") or "Nairobi",
        "rep_name": user.name,
        "rep_email": user.email,
    }


def active_sender(*, db, sender_identity_id):
    sender = message_repository.get_sender_identity(
        db=db,
        sender_identity_id=sender_identity_id,
    )
    if sender is None:
        raise MessageResourceNotFoundError("Sender identity not found")
    if not sender.get("is_active"):
        raise SenderUnavailableError("Sender identity is inactive")
    return sender


def enforce_delivery_policy(recipient_email):
    if os.getenv("APP_ENV", "development").lower() != "staging":
        return
    if os.getenv("EMAIL_DELIVERY_MODE", "normal").lower() != "allowlist":
        return

    allowed = {
        value.strip().lower()
        for value in os.getenv("EMAIL_ALLOWED_RECIPIENTS", "").split(",")
        if value.strip()
    }
    if recipient_email.strip().lower() not in allowed:
        raise RecipientNotAllowedError(
            "Recipient is not in the staging email allowlist"
        )


def decode_attachment(payload):
    if not payload.attachment_b64:
        return None
    try:
        data = base64.b64decode(payload.attachment_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MessageValidationError(
            "Invalid base64 attachment data"
        ) from exc
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise MessageValidationError("Attachment exceeds the 5 MB limit")
    return OutboundAttachment(
        filename=payload.attachment_name or "attachment",
        content_type=(
            payload.attachment_content_type
            or "application/octet-stream"
        ),
        data=data,
    )


def preview_message(*, db, payload, user):
    lead = message_repository.get_lead(db=db, lead_id=payload.lead_id)
    if lead is None:
        raise MessageResourceNotFoundError("Lead not found")

    template = message_repository.get_template(
        db=db,
        template_id=payload.template_id,
    )
    if template is None:
        raise MessageResourceNotFoundError(
            "Communication template not found"
        )
    if not template.get("is_active"):
        raise MessageValidationError(
            "Communication template is inactive"
        )

    sender = active_sender(
        db=db,
        sender_identity_id=payload.sender_identity_id,
    )
    subject, body_text = render_template(
        subject=template["subject"],
        body=template["body_text"],
        values=context_for(lead, user),
    )
    to_email = payload.to_email or lead.get("email")
    if not to_email:
        raise MessageValidationError("Lead has no email address")

    return {
        "lead_id": lead["id"],
        "template_id": template["id"],
        "to_email": to_email.strip().lower(),
        "recipient_name": (
            lead.get("contact_person")
            or lead.get("owner_name")
            or "Property Manager"
        ),
        "subject": payload.subject_override or subject,
        "body_text": payload.body_override or body_text,
        "body_html": template.get("body_html"),
        "sender_identity": {
            "id": sender["id"],
            "display_name": sender["display_name"],
            "email_address": sender["email_address"],
            "reply_to_address": sender.get("reply_to_address"),
            "provider": sender["provider"],
        },
        "smtp_configured": SMTPEmailProvider().configured,
    }


def send_message(*, db, payload, user, provider=None):
    existing = message_repository.find_message_by_idempotency_key(
        db=db,
        idempotency_key=payload.idempotency_key,
    )
    if existing is not None:
        return existing

    enforce_delivery_policy(payload.to_email)

    if message_repository.is_recipient_suppressed(
        db=db,
        email_address=payload.to_email,
    ):
        raise RecipientSuppressedError("Recipient is suppressed")

    sender = active_sender(
        db=db,
        sender_identity_id=payload.sender_identity_id,
    )
    attachment = decode_attachment(payload)
    queued = message_repository.create_queued_message(
        db=db,
        values={
            "lead_id": payload.lead_id,
            "template_id": payload.template_id,
            "sender_identity_id": payload.sender_identity_id,
            "sent_by": user.id,
            "recipient_email": payload.to_email,
            "recipient_name": payload.recipient_name,
            "subject": payload.subject,
            "body_text": payload.body_text,
            "body_html": payload.body_html,
            "message_type": "manual",
            "idempotency_key": payload.idempotency_key,
            "attachment_name": (
                attachment.filename if attachment else None
            ),
            "attachment_content_type": (
                attachment.content_type if attachment else None
            ),
            "attachment_size": (
                len(attachment.data) if attachment else None
            ),
            "follow_up_date": payload.follow_up_date,
        },
    )
    db.commit()

    result = (provider or SMTPEmailProvider()).send(
        OutboundMessage(
            from_name=sender["display_name"],
            from_email=sender["email_address"],
            reply_to=sender.get("reply_to_address"),
            to_email=payload.to_email,
            subject=payload.subject,
            body_text=payload.body_text,
            body_html=payload.body_html,
            attachment=attachment,
        )
    )

    if not result.accepted:
        error = result.error_message or "Email provider rejected the message"
        message_repository.mark_message_failed(
            db=db,
            message_id=queued["id"],
            error_message=error,
        )
        db.commit()
        return message_repository.get_message(
            db=db,
            message_id=queued["id"],
        ) or {**queued, "status": "failed", "error_message": error}

    message_repository.mark_message_sent(
        db=db,
        message_id=queued["id"],
        provider_message_id=result.provider_message_id,
    )
    if payload.lead_id is not None:
        message_repository.update_lead_after_successful_send(
            db=db,
            lead_id=payload.lead_id,
            follow_up_date=payload.follow_up_date,
            changed_by=user.name,
            recipient_email=payload.to_email,
        )
    db.commit()
    return message_repository.get_message(
        db=db,
        message_id=queued["id"],
    ) or {
        **queued,
        "status": "sent",
        "provider_message_id": result.provider_message_id,
    }


def send_test_message(*, db, payload, user, provider=None):
    return send_message(
        db=db,
        payload=MessageSendRequest(
            sender_identity_id=payload.sender_identity_id,
            to_email=payload.to_email,
            subject=payload.subject,
            body_text=payload.body_text,
            idempotency_key=payload.idempotency_key,
        ),
        user=user,
        provider=provider,
    )
