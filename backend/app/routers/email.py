"""
email.py — Email outreach + follow-up system with attachment support

Endpoints:
    POST /api/leads/{id}/email/preview        — generate preview
    POST /api/leads/{id}/email/send           — send with optional attachment
    POST /api/leads/{id}/email/send-with-file — send with file upload
    GET  /api/leads/{id}/emails               — email history
    GET  /api/emails/outreach                 — manager view
"""

import os
import logging
import smtplib
import base64
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from random import randint
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, CurrentUser

router = APIRouter(prefix="/api", tags=["email"])
logger = logging.getLogger(__name__)

# ── SMTP config from environment ──────────────────────────────────
SMTP_HOST      = os.getenv("SMTP_HOST", "")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER      = os.getenv("SMTP_USER", "")
SMTP_PASSWORD  = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Nyumba Zetu Sales")
MOCK_MODE      = not bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


# ── Templates ─────────────────────────────────────────────────────

COLD_TEMPLATES = {
    "template_1": {
        "subject": "Request for Demo Meeting — Nyumba Zetu Property Management",
        "body": """Good morning {contact_name},

I hope you're doing well. I'm {rep_name} from Nyumba Zetu, reaching out regarding {company_name}.

Our property management system streamlines your operations — from rent collection and maintenance requests to lease renewals and accounting — ensuring your properties run efficiently while maximizing returns.

I would love to arrange a demo at your convenience, whether virtual or in-person. I'll walk you through the key features and address any questions you may have.

Could you let me know a day and time that works best for you?

Best regards,
{rep_name}
Nyumba Zetu
{rep_email}""",
    },
    "template_2": {
        "subject": "Discover a Smarter Property Management Solution — Nyumba Zetu",
        "body": """Good morning {contact_name},

I trust this email finds you well. I'm {rep_name} from Nyumba Zetu.

Our property management system streamlines everything from rent collection to maintenance requests while enhancing tenant satisfaction for properties in {area}.

I'd love to schedule a brief call to show you how Nyumba Zetu can specifically benefit {company_name}. Could we connect this week?

Best regards,
{rep_name}
Nyumba Zetu
{rep_email}""",
    },
    "template_3": {
        "subject": "Revolutionize Your Property Management with Nyumba Zetu",
        "body": """Hi {contact_name},

I hope this email finds you well. I'm {rep_name} from Nyumba Zetu, a leading provider of innovative property management solutions.

We believe our platform can add significant value to {company_name}'s operations — offering automated workflows, streamlined rent collection, maintenance tracking, and in-depth analytics.

Could we schedule a brief discovery call at your convenience?

Looking forward to connecting,
{rep_name}
Nyumba Zetu
{rep_email}""",
    },
    "template_4": {
        "subject": "Unlock Efficiency in Property Management with Nyumba Zetu",
        "body": """Dear {contact_name},

I trust this email finds you in good spirits. I'm reaching out from Nyumba Zetu to introduce a solution that helps property managers like yourself optimize operations, reduce costs, and deliver exceptional service to tenants.

I'd love to share how our platform can be tailored to meet {company_name}'s unique needs.

Could we schedule a brief discovery call at your convenience?

Warm regards,
{rep_name}
Nyumba Zetu
{rep_email}""",
    },
    "template_5": {
        "subject": "Elevate Your Property Management Experience with Nyumba Zetu",
        "body": """Hello {contact_name},

I hope this message finds you well. I'm {rep_name} from Nyumba Zetu.

I've been impressed by {company_name} and believe our platform can add significant value to your operations in {area} — offering everything from automated workflows to in-depth analytics.

Could we schedule a brief call to discuss how our solution aligns with your goals?

Best regards,
{rep_name}
Nyumba Zetu
{rep_email}""",
    },
}

FOLLOWUP_TEMPLATE = {
    "subject": "Following Up — Nyumba Zetu Property Management",
    "body": """Hi {contact_name},

I hope you're doing well. I'm following up on my previous email regarding Nyumba Zetu's property management solution for {company_name}.

I wanted to check if you had a chance to consider how our platform could streamline your operations in {area}. We've helped similar property managers save significant time on rent collection and maintenance tracking.

Would you be available for a quick 15-minute call this week?

Best regards,
{rep_name}
Nyumba Zetu
{rep_email}""",
}


def fill_template(template: dict, context: dict) -> dict:
    return {
        "subject": template["subject"].format(**context),
        "body":    template["body"].format(**context),
    }


def load_templates_from_db(db) -> dict:
    """
    Load templates from database settings.
    Falls back to hardcoded templates if DB has no settings.
    """
    try:
        from sqlalchemy import text
        rows = db.execute(text("""
            SELECT key, value FROM email_settings
            WHERE key LIKE 'template_%'
        """)).fetchall()

        if not rows:
            return None

        settings = {r.key: r.value for r in rows}

        cold = {}
        for i in range(1, 6):
            key = f"template_cold_{i}"
            subj = settings.get(f"{key}_subject")
            body = settings.get(f"{key}_body")
            if subj and body:
                cold[f"template_{i}"] = {"subject": subj, "body": body}

        followup_subj = settings.get("template_followup_subject")
        followup_body = settings.get("template_followup_body")

        return {
            "cold":    cold if cold else None,
            "followup": {
                "subject": followup_subj,
                "body":    followup_body,
            } if followup_subj and followup_body else None,
        }
    except Exception as e:
        logger.warning(f"Could not load templates from DB: {e}")
        return None


def build_context(lead: dict, user: CurrentUser) -> dict:
    return {
        "contact_name": (
            lead.get("contact_person") or
            lead.get("owner_name") or
            "Property Manager"
        ),
        "company_name": lead.get("name", "your company"),
        "area":         lead.get("area") or "Nairobi",
        "rep_name":     user.name,
        "rep_email":    user.email,
    }


def send_smtp(
    from_email: str,
    to_email: str,
    subject: str,
    body: str,
    attachment_data: bytes = None,
    attachment_name: str = None,
    attachment_type: str = "application/pdf",
) -> bool:
    """
    Send email via SMTP with optional attachment.
    In mock mode logs the email without sending.
    """
    if MOCK_MODE:
        logger.info(
            f"[MOCK] From:{from_email} → To:{to_email} | "
            f"{subject[:50]} | attachment:{attachment_name or 'none'}"
        )
        return True

    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"]    = f"{SMTP_FROM_NAME} <{from_email}>"
        msg["To"]      = to_email
        msg["Reply-To"] = from_email

        msg.attach(MIMEText(body, "plain"))

        # Add attachment if provided
        if attachment_data and attachment_name:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment_data)
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename={attachment_name}"
            )
            msg.attach(part)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(from_email, to_email, msg.as_string())

        logger.info(
            f"Email sent: {from_email} → {to_email}"
            f"{' (+attachment)' if attachment_name else ''}"
        )
        return True

    except Exception as e:
        logger.error(f"SMTP error: {e}")
        return False


# ── Schemas ───────────────────────────────────────────────────────

class PreviewRequest(BaseModel):
    email_type:    str = "cold"
    template_name: str = "template_1"
    custom_body:   Optional[str] = None


class SendRequest(BaseModel):
    email_id:        int
    to_email:        Optional[str] = None
    final_body:      str
    attachment_name: Optional[str] = None
    attachment_b64:  Optional[str] = None  # base64 encoded file


# ── Endpoints ─────────────────────────────────────────────────────

@router.post("/leads/{lead_id}/email/preview")
def preview_email(
    lead_id: int,
    body:    PreviewRequest,
    db:      Session = Depends(get_db),
    user:    CurrentUser = Depends(get_current_user),
):
    """Generate email preview for rep to review before sending."""
    lead_row = db.execute(text("""
        SELECT id, name, owner_name, email, phone,
               area, lead_type, contact_person, website
        FROM leads WHERE id = :id
    """), {"id": lead_id}).fetchone()

    if not lead_row:
        raise HTTPException(404, "Lead not found")

    lead    = dict(lead_row._mapping)
    context = build_context(lead, user)

    # Load templates from DB, fall back to hardcoded
    db_templates = load_templates_from_db(db)

    if body.email_type == "followup":
        template = (
            db_templates["followup"]
            if db_templates and db_templates.get("followup")
            else FOLLOWUP_TEMPLATE
        )
        template_name = "followup"
    else:
        cold_source = (
            db_templates["cold"]
            if db_templates and db_templates.get("cold")
            else COLD_TEMPLATES
        )
        template = cold_source.get(
            body.template_name, cold_source.get("template_1", COLD_TEMPLATES["template_1"])
        )
        template_name = body.template_name

    filled     = fill_template(template, context)
    final_body = body.custom_body or filled["body"]
    follow_up_date = (
        date.today() + timedelta(days=randint(7, 10))
    ).isoformat()

    row = db.execute(text("""
        INSERT INTO email_outreach (
            lead_id, sent_by, sent_from, sent_to,
            subject, body, email_type, template_used,
            status, follow_up_date, created_at
        ) VALUES (
            :lead_id, :sent_by, :sent_from, :sent_to,
            :subject, :body, :email_type, :template_used,
            'draft', :follow_up_date, NOW()
        )
        RETURNING id
    """), {
        "lead_id":        lead_id,
        "sent_by":        user.id,
        "sent_from":      user.email,
        "sent_to":        lead.get("email"),
        "subject":        filled["subject"],
        "body":           final_body,
        "email_type":     body.email_type,
        "template_used":  template_name,
        "follow_up_date": follow_up_date,
    }).fetchone()
    db.commit()

    return {
        "email_id":       row.id,
        "from":           user.email,
        "from_display":   f"{SMTP_FROM_NAME} <{user.email}>",
        "to":             lead.get("email"),
        "to_name":        context["contact_name"],
        "company":        lead.get("name"),
        "subject":        filled["subject"],
        "body":           final_body,
        "email_type":     body.email_type,
        "template_used":  template_name,
        "has_email":      bool(lead.get("email")),
        "follow_up_date": follow_up_date,
        "status":         "draft",
        "mock_mode":      MOCK_MODE,
        "smtp_configured": not MOCK_MODE,
        "available_templates": list(COLD_TEMPLATES.keys()),
    }


@router.post("/leads/{lead_id}/email/send")
def send_email(
    lead_id: int,
    body:    SendRequest,
    db:      Session = Depends(get_db),
    user:    CurrentUser = Depends(get_current_user),
):
    """
    Confirm and send the previewed email.
    Supports base64-encoded file attachment via attachment_b64.
    """
    draft = db.execute(text("""
        SELECT id, sent_from, sent_to, subject,
               email_type, follow_up_date
        FROM email_outreach
        WHERE id = :id AND lead_id = :lead_id AND status = 'draft'
    """), {"id": body.email_id, "lead_id": lead_id}).fetchone()

    if not draft:
        raise HTTPException(404, "Draft not found — generate a preview first")

    to_email = body.to_email or draft.sent_to
    if not to_email:
        raise HTTPException(400,
            "No email address — provide to_email in the request body"
        )

    # Decode attachment if provided
    attachment_data = None
    if body.attachment_b64 and body.attachment_name:
        try:
            attachment_data = base64.b64decode(body.attachment_b64)
        except Exception:
            raise HTTPException(400, "Invalid base64 attachment data")

    sent   = send_smtp(
        from_email      = draft.sent_from,
        to_email        = to_email,
        subject         = draft.subject,
        body            = body.final_body,
        attachment_data = attachment_data,
        attachment_name = body.attachment_name,
    )
    status = "sent" if sent else "failed"

    # Update draft
    db.execute(text("""
        UPDATE email_outreach SET
            body              = :body,
            sent_to           = :sent_to,
            status            = :status,
            sent_at           = NOW()
        WHERE id = :id
    """), {
        "body":    body.final_body,
        "sent_to": to_email,
        "status":  status,
        "id":      body.email_id,
    })

    # Update lead
    db.execute(text("""
        UPDATE leads SET
            last_contacted   = NOW(),
            follow_up_date   = :follow_up_date,
            contact_attempts = COALESCE(contact_attempts, 0) + 1,
            email_sent_at    = NOW(),
            updated_at       = NOW()
        WHERE id = :id
    """), {"id": lead_id, "follow_up_date": draft.follow_up_date})

    # Log to timeline
    db.execute(text("""
        INSERT INTO lead_events (
            lead_id, event_type, to_value,
            changed_by, note, created_at
        ) VALUES (
            :lead_id, 'email_sent', :to_email,
            :by, :note, NOW()
        )
    """), {
        "lead_id":  lead_id,
        "to_email": to_email,
        "by":       user.name,
        "note": (
            f"{draft.email_type} email sent by {user.name}"
            f"{' with attachment' if attachment_data else ''}"
        ),
    })
    db.commit()

    return {
        "status":          status,
        "sent_to":         to_email,
        "sent_from":       draft.sent_from,
        "email_type":      draft.email_type,
        "has_attachment":  bool(attachment_data),
        "attachment_name": body.attachment_name,
        "follow_up_date":  draft.follow_up_date.isoformat() if draft.follow_up_date else None,
        "mock_mode":       MOCK_MODE,
        "message": (
            "Email logged — set SMTP_HOST, SMTP_USER, SMTP_PASSWORD in .env to send real emails"
            if MOCK_MODE else
            f"Email sent to {to_email}"
            + (f" with {body.attachment_name}" if body.attachment_name else "")
        ),
    }


@router.post("/leads/{lead_id}/email/send-with-file")
async def send_email_with_file(
    lead_id:    int,
    email_id:   int = Form(...),
    final_body: str = Form(...),
    to_email:   Optional[str] = Form(None),
    attachment: Optional[UploadFile] = File(None),
    db:         Session = Depends(get_db),
    user:       CurrentUser = Depends(get_current_user),
):
    """
    Send email with a real file upload (multipart form).
    Use this endpoint when the rep uploads a PDF or document.
    Max file size: 5MB.
    """
    draft = db.execute(text("""
        SELECT id, sent_from, sent_to, subject,
               email_type, follow_up_date
        FROM email_outreach
        WHERE id = :id AND lead_id = :lead_id AND status = 'draft'
    """), {"id": email_id, "lead_id": lead_id}).fetchone()

    if not draft:
        raise HTTPException(404, "Draft not found")

    recipient = to_email or draft.sent_to
    if not recipient:
        raise HTTPException(400, "No email address provided")

    # Read attachment
    attachment_data = None
    attachment_name = None
    if attachment and attachment.filename:
        attachment_data = await attachment.read()
        attachment_name = attachment.filename
        # 5MB limit
        if len(attachment_data) > 5 * 1024 * 1024:
            raise HTTPException(400, "Attachment too large — maximum 5MB")

    sent   = send_smtp(
        from_email      = draft.sent_from,
        to_email        = recipient,
        subject         = draft.subject,
        body            = final_body,
        attachment_data = attachment_data,
        attachment_name = attachment_name,
    )
    status = "sent" if sent else "failed"

    db.execute(text("""
        UPDATE email_outreach SET
            body    = :body,
            sent_to = :sent_to,
            status  = :status,
            sent_at = NOW()
        WHERE id = :id
    """), {"body": final_body, "sent_to": recipient,
           "status": status, "id": email_id})

    db.execute(text("""
        UPDATE leads SET
            last_contacted   = NOW(),
            follow_up_date   = :follow_up_date,
            contact_attempts = COALESCE(contact_attempts, 0) + 1,
            email_sent_at    = NOW(),
            updated_at       = NOW()
        WHERE id = :id
    """), {"id": lead_id, "follow_up_date": draft.follow_up_date})

    db.execute(text("""
        INSERT INTO lead_events (
            lead_id, event_type, to_value,
            changed_by, note, created_at
        ) VALUES (
            :lead_id, 'email_sent', :to_email,
            :by, :note, NOW()
        )
    """), {
        "lead_id":  lead_id,
        "to_email": recipient,
        "by":       user.name,
        "note": (
            f"{draft.email_type} email sent by {user.name}"
            f"{f' + {attachment_name}' if attachment_name else ''}"
        ),
    })
    db.commit()

    return {
        "status":          status,
        "sent_to":         recipient,
        "has_attachment":  bool(attachment_data),
        "attachment_name": attachment_name,
        "follow_up_date":  draft.follow_up_date.isoformat() if draft.follow_up_date else None,
        "mock_mode":       MOCK_MODE,
        "message": (
            "Email logged in mock mode"
            if MOCK_MODE else
            f"Email sent to {recipient}"
        ),
    }


@router.get("/leads/{lead_id}/emails")
def get_lead_emails(
    lead_id: int,
    db:      Session = Depends(get_db),
    user:    CurrentUser = Depends(get_current_user),
):
    """Email history for a lead — powers the contact timeline."""
    rows = db.execute(text("""
        SELECT e.id, e.sent_from, e.sent_to, e.subject,
               e.body, e.email_type, e.template_used,
               e.status, e.sent_at, e.follow_up_date,
               e.created_at, u.name AS sent_by_name
        FROM email_outreach e
        LEFT JOIN users u ON u.id = e.sent_by
        WHERE e.lead_id = :lead_id AND e.status != 'draft'
        ORDER BY e.created_at DESC
    """), {"lead_id": lead_id}).fetchall()

    return [
        {
            "id":             r.id,
            "sent_from":      r.sent_from,
            "sent_to":        r.sent_to,
            "subject":        r.subject,
            "body":           r.body,
            "email_type":     r.email_type,
            "template_used":  r.template_used,
            "status":         r.status,
            "sent_by":        r.sent_by_name,
            "sent_at":        r.sent_at.isoformat() if r.sent_at else None,
            "follow_up_date": r.follow_up_date.isoformat() if r.follow_up_date else None,
            "created_at":     r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/emails/outreach")
def get_all_outreach(
    db:   Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """All sent emails — manager overview."""
    rows = db.execute(text("""
        SELECT e.id, e.lead_id, l.name AS lead_name,
               l.area, e.sent_from, e.sent_to,
               e.subject, e.email_type, e.status,
               e.sent_at, u.name AS sent_by_name
        FROM email_outreach e
        LEFT JOIN leads l ON l.id = e.lead_id
        LEFT JOIN users u ON u.id = e.sent_by
        WHERE e.status = 'sent'
        ORDER BY e.sent_at DESC
        LIMIT 100
    """)).fetchall()

    return [
        {
            "id":         r.id,
            "lead_id":    r.lead_id,
            "lead_name":  r.lead_name,
            "area":       r.area,
            "sent_from":  r.sent_from,
            "sent_to":    r.sent_to,
            "subject":    r.subject,
            "email_type": r.email_type,
            "status":     r.status,
            "sent_by":    r.sent_by_name,
            "sent_at":    r.sent_at.isoformat() if r.sent_at else None,
        }
        for r in rows
    ]


@router.get("/email/config")
def get_email_config(
    user: CurrentUser = Depends(get_current_user),
):
    """Returns current email configuration status — no secrets exposed."""
    return {
        "mock_mode":       MOCK_MODE,
        "smtp_configured": not MOCK_MODE,
        "smtp_host":       SMTP_HOST or "not set",
        "smtp_user":       SMTP_USER or "not set",
        "from_name":       SMTP_FROM_NAME,
        "message": (
            "Running in mock mode — emails logged but not sent. "
            "Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD in .env to enable."
        ) if MOCK_MODE else (
            f"SMTP configured — sending from {SMTP_USER}"
        ),
    }
