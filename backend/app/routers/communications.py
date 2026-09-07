"""
communications.py — Bulk email and newsletter system
Powered by Resend for reliable delivery and tracking.

Endpoints:
    POST /api/comms/campaigns              — create campaign
    GET  /api/comms/campaigns              — list campaigns
    GET  /api/comms/campaigns/{id}         — campaign detail
    POST /api/comms/campaigns/{id}/send    — send campaign
    GET  /api/comms/campaigns/{id}/recipients — recipient list

    POST /api/comms/lists                  — create mailing list
    GET  /api/comms/lists                  — list all mailing lists
    GET  /api/comms/lists/{id}             — list detail + contacts
    POST /api/comms/lists/{id}/contacts    — add contacts
    DELETE /api/comms/lists/{id}/contacts/{email} — remove contact

    POST /api/comms/unsubscribe            — unsubscribe email
    GET  /api/comms/preview                — preview campaign email
"""

import os
import base64
import json
import logging
import httpx
import re
from datetime import datetime, timezone
from typing import Optional, List, Literal
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, CurrentUser
from pathlib import Path

router = APIRouter(prefix="/api/comms", tags=["communications"])
logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"

RECIPIENT_FILTER_FIELDS = frozenset({
    "area",
    "lead_type",
    "status",
    "ai_score",
})


def _validate_recipient_filter(recipient_filter: dict) -> dict:
    if not isinstance(recipient_filter, dict):
        raise HTTPException(
            status_code=400,
            detail="Campaign audience filter must be a JSON object",
        )

    unsupported = sorted(set(recipient_filter) - RECIPIENT_FILTER_FIELDS)
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported audience filter(s): "
                + ", ".join(unsupported)
            ),
        )

    clean = {}
    for key, value in recipient_filter.items():
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise HTTPException(
                status_code=400,
                detail=f"Audience filter '{key}' must be a string",
            )

        value = value.strip()
        if value:
            clean[key] = value

    return clean


def _serialize_recipient_filter(recipient_filter) -> Optional[str]:
    if not recipient_filter:
        return None

    clean = _validate_recipient_filter(recipient_filter)
    return json.dumps(clean) if clean else None


def _parse_recipient_filter(recipient_filter) -> dict:
    if not recipient_filter:
        return {}

    if isinstance(recipient_filter, dict):
        parsed = recipient_filter
    elif isinstance(recipient_filter, str):
        try:
            parsed = json.loads(recipient_filter)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Campaign audience filter is invalid JSON; "
                    "review the campaign before sending"
                ),
            ) from exc
    else:
        raise HTTPException(
            status_code=400,
            detail="Campaign audience filter has an invalid format",
        )

    return _validate_recipient_filter(parsed)


def _get_resend_key() -> str:
    try:
        from app.config import settings
        return getattr(settings, "RESEND_API_KEY", "") or os.getenv("RESEND_API_KEY", "")
    except Exception:
        return os.getenv("RESEND_API_KEY", "")

def _get_comms_from_email() -> str:
    try:
        from app.config import settings
        return getattr(settings, "COMMS_FROM_EMAIL", "") or os.getenv("COMMS_FROM_EMAIL", "onboarding@resend.dev")
    except Exception:
        return os.getenv("COMMS_FROM_EMAIL", "onboarding@resend.dev")

def _get_app_url() -> str:
    try:
        from app.config import settings
        return getattr(settings, "APP_URL", "") or os.getenv("APP_URL", "https://nyumba-lead-hub.vercel.app")
    except Exception:
        return os.getenv("APP_URL", "https://nyumba-lead-hub.vercel.app")


# ── Email validation ──────────────────────────────────────────────

def is_valid_email(email: str) -> bool:
    pattern = r'^[\w\.\-]+@[\w\.\-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email.strip()))


def _delivery_outcome(
    sent_count: int,
    failed_count: int,
) -> str:
    if failed_count == 0:
        return "sent"

    if sent_count == 0:
        return "failed"

    return "sent_with_issues"


def is_unsubscribed(db: Session, email: str) -> bool:
    row = db.execute(
        text("SELECT 1 FROM unsubscribes WHERE email = :email"),
        {"email": email.lower().strip()}
    ).fetchone()
    return row is not None


# ── Campaign attachments ──────────────────────────────────────────

MAX_CAMPAIGN_ATTACHMENT_SIZE = 5 * 1024 * 1024

ALLOWED_CAMPAIGN_ATTACHMENT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
}


def validate_campaign_attachment(
    filename: str,
    content: bytes,
    content_type: str,
) -> dict:
    """Validate an uploaded campaign attachment."""
    extension = Path(filename or "").suffix.lower()

    if extension not in ALLOWED_CAMPAIGN_ATTACHMENT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported attachment type",
        )

    if len(content) > MAX_CAMPAIGN_ATTACHMENT_SIZE:
        raise HTTPException(
            status_code=400,
            detail="Attachment too large — maximum 5MB",
        )

    return {
        "filename": filename,
        "content": content,
        "content_type": content_type,
        "size": len(content),
    }




def ensure_campaign_attachment_editable(status: str) -> None:
    """Attachments may only change before campaign sending starts."""
    if status not in ("draft", "reviewed"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot change attachment for campaign "
                f"with status '{status}'"
            ),
        )


def campaign_attachment_update_values(
    filename,
    content,
    content_type,
) -> dict:
    """Return campaign attachment fields ready for database persistence."""
    if filename is None or content is None:
        return {
            "attachment_name": None,
            "attachment_content": None,
            "attachment_mime_type": None,
            "attachment_size": None,
        }

    return {
        "attachment_name": filename,
        "attachment_content": content,
        "attachment_mime_type": content_type,
        "attachment_size": len(content),
    }


def build_resend_attachment(
    filename: str,
    content: bytes,
) -> dict:
    """Build the attachment payload expected by Resend."""
    return {
        "filename": filename,
        "content": base64.b64encode(content).decode("ascii"),
    }


# ── Resend sender ─────────────────────────────────────────────────



def campaign_attachment_send_kwargs(campaign: dict) -> dict:
    """Forward a campaign's saved attachment to the email sender."""
    return {
        "attachment_name": campaign.get("attachment_name"),
        "attachment_content": campaign.get("attachment_content"),
    }


def build_resend_payload(
    to_email: str,
    from_email: str,
    from_name: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    reply_to: Optional[str] = None,
    attachment_name: Optional[str] = None,
    attachment_content: Optional[bytes] = None,
) -> dict:
    """Build the Resend API payload for a campaign email."""
    payload = {
        "from": f"{from_name} <{from_email}>",
        "to": [to_email],
        "subject": subject,
        "text": body,
    }

    if html_body:
        payload["html"] = html_body

    if reply_to:
        payload["reply_to"] = reply_to

    if attachment_name and attachment_content is not None:
        payload["attachments"] = [
            build_resend_attachment(
                filename=attachment_name,
                content=attachment_content,
            )
        ]

    return payload


async def send_via_resend(
    to_email:    str,
    to_name:     str,
    from_email:  str,
    from_name:   str,
    subject:     str,
    body:        str,
    html_body:   Optional[str] = None,
    reply_to:    Optional[str] = None,
    append_unsubscribe_footer: bool = True,
    attachment_name: Optional[str] = None,
    attachment_content: Optional[bytes] = None,
) -> dict:
    """Send a single email via Resend API."""
    api_key  = _get_resend_key().strip()
    app_url  = _get_app_url()

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "Bulk email is not configured. "
                "RESEND_API_KEY is required before sending emails."
            ),
        )

    payload = build_resend_payload(
        to_email=to_email,
        from_email=from_email,
        from_name=from_name,
        subject=subject,
        body=body,
        html_body=html_body,
        reply_to=reply_to,
        attachment_name=attachment_name,
        attachment_content=attachment_content,
    )

    # Cold outreach gets the standard plain-text unsubscribe footer.
    # Newsletter text/html already contains its own personalized footer.
    if append_unsubscribe_footer:
        unsubscribe_url = (
            f"{app_url}/unsubscribe?email={to_email}"
        )
        payload["text"] += (
            f"\n\n---\nTo unsubscribe, visit: "
            f"{unsubscribe_url}"
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            RESEND_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            json=payload,
        )

    if resp.status_code in (200, 201):
        return resp.json()
    else:
        raise Exception(f"Resend error {resp.status_code}: {resp.text[:200]}")


def personalise(template: str, context: dict) -> str:
    """Replace placeholders in template body."""
    for key, val in context.items():
        template = template.replace(f"{{{key}}}", val or "")
    return template


# ── Schemas ───────────────────────────────────────────────────────

class CampaignCreate(BaseModel):
    name:            str
    subject:         str
    body:            str
    html_body:       Optional[str] = None
    communication_type: Literal["cold_outreach", "newsletter"]
    sender_name:     str = "Nyumba Zetu"
    sender_email:    str = "onboarding@resend.dev"
    reply_to:        Optional[str] = None
    recipient_type:  str  # leads / mailing_list / csv_upload
    mailing_list_id: Optional[int] = None
    recipient_filter: Optional[dict] = None
    # e.g. {"lead_type": "agency", "area": "Kilimani", "status": "new"}

class CampaignUpdate(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    html_body: Optional[str] = None
    sender_name: Optional[str] = None
    sender_email: Optional[str] = None
    reply_to: Optional[str] = None


class MailingListCreate(BaseModel):
    name:        str
    description: Optional[str] = None


class ContactAdd(BaseModel):
    contacts: List[dict]
    # each: { "email": "...", "name": "..." }

class CampaignRecipientUpload(BaseModel):
    recipients: List[dict]



class UnsubscribeRequest(BaseModel):
    email: str
    reason: Optional[str] = None


# ── Mailing list endpoints ────────────────────────────────────────

@router.post("/lists")
def create_list(
    body: MailingListCreate,
    db:   Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    row = db.execute(text("""
        INSERT INTO mailing_lists (name, description, created_by)
        VALUES (:name, :description, :created_by)
        RETURNING id, name
    """), {
        "name":        body.name,
        "description": body.description,
        "created_by":  user.name,
    }).fetchone()
    db.commit()
    return {"id": row.id, "name": row.name, "message": "Mailing list created ✅"}


@router.get("/lists")
def get_lists(
    db:   Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    rows = db.execute(text("""
        SELECT m.id, m.name, m.description, m.created_by, m.created_at,
               COUNT(c.id) FILTER (WHERE c.unsubscribed = FALSE) AS active_contacts,
               COUNT(c.id) AS total_contacts
        FROM mailing_lists m
        LEFT JOIN mailing_list_contacts c ON c.list_id = m.id
        GROUP BY m.id
        ORDER BY m.created_at DESC
    """)).fetchall()

    return [
        {
            "id":              r.id,
            "name":            r.name,
            "description":     r.description,
            "created_by":      r.created_by,
            "active_contacts": r.active_contacts,
            "total_contacts":  r.total_contacts,
            "created_at":      r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/lists/{list_id}")
def get_list(
    list_id: int,
    db:      Session = Depends(get_db),
    user:    CurrentUser = Depends(get_current_user),
):
    ml = db.execute(text("""
        SELECT id, name, description, created_by, created_at
        FROM mailing_lists WHERE id = :id
    """), {"id": list_id}).fetchone()

    if not ml:
        raise HTTPException(404, "Mailing list not found")

    contacts = db.execute(text("""
        SELECT email, name, source, unsubscribed, created_at
        FROM mailing_list_contacts
        WHERE list_id = :list_id
        ORDER BY created_at DESC
    """), {"list_id": list_id}).fetchall()

    return {
        "id":          ml.id,
        "name":        ml.name,
        "description": ml.description,
        "created_by":  ml.created_by,
        "contacts": [
            {
                "email":        c.email,
                "name":         c.name,
                "source":       c.source,
                "unsubscribed": c.unsubscribed,
                "created_at":   c.created_at.isoformat() if c.created_at else None,
            }
            for c in contacts
        ],
    }


@router.post("/lists/{list_id}/contacts")
def add_contacts(
    list_id: int,
    body:    ContactAdd,
    db:      Session = Depends(get_db),
    user:    CurrentUser = Depends(get_current_user),
):
    """Add contacts to a mailing list — validates and deduplicates."""
    added = 0
    skipped = 0
    invalid = []

    for c in body.contacts:
        email = str(c.get("email", "")).strip().lower()
        name  = str(c.get("name", "")).strip() or None

        if not is_valid_email(email):
            invalid.append(email)
            continue

        try:
            db.execute(text("""
                INSERT INTO mailing_list_contacts (list_id, email, name, source)
                VALUES (:list_id, :email, :name, 'manual')
                ON CONFLICT (list_id, email) DO NOTHING
            """), {"list_id": list_id, "email": email, "name": name})
            added += 1
        except Exception:
            skipped += 1

    db.commit()
    return {
        "added":   added,
        "skipped": skipped,
        "invalid": invalid,
        "message": f"{added} contacts added",
    }


@router.post("/lists/{list_id}/import-leads")
def import_leads_to_list(
    list_id:     int,
    lead_type:   Optional[str] = None,
    area:        Optional[str] = None,
    status:      Optional[str] = None,
    db:          Session = Depends(get_db),
    user:        CurrentUser = Depends(get_current_user),
):
    """Import leads with emails directly into a mailing list."""
    filters = ["email IS NOT NULL", "email != ''"]
    params  = {"list_id": list_id}

    if lead_type:
        filters.append("lead_type = :lead_type")
        params["lead_type"] = lead_type
    if area:
        filters.append("area ILIKE :area")
        params["area"] = f"%{area}%"
    if status:
        filters.append("status = :status")
        params["status"] = status

    where = " AND ".join(filters)

    leads = db.execute(text(f"""
        SELECT id, name, email FROM leads
        WHERE {where}
    """), params).fetchall()

    added = 0
    for lead in leads:
        email = lead.email.strip().lower()
        if not is_valid_email(email):
            continue
        try:
            db.execute(text("""
                INSERT INTO mailing_list_contacts
                    (list_id, email, name, lead_id, source)
                VALUES (:list_id, :email, :name, :lead_id, 'leads')
                ON CONFLICT (list_id, email) DO NOTHING
            """), {
                "list_id": list_id,
                "email":   email,
                "name":    lead.name,
                "lead_id": lead.id,
            })
            added += 1
        except Exception:
            pass

    db.commit()
    return {
        "added":   added,
        "total":   len(leads),
        "message": f"{added} leads imported to mailing list",
    }


# ── Campaign attachment endpoints ──────────────────────────────────

@router.post("/campaigns/{campaign_id}/attachment")
async def upload_campaign_attachment(
    campaign_id: int,
    attachment: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    campaign = db.execute(
        text("""
            SELECT id, status
            FROM campaigns
            WHERE id = :id
        """),
        {"id": campaign_id},
    ).fetchone()

    if not campaign:
        raise HTTPException(404, "Campaign not found")

    ensure_campaign_attachment_editable(campaign.status)

    content = await attachment.read()

    validated = validate_campaign_attachment(
        filename=attachment.filename or "",
        content=content,
        content_type=(
            attachment.content_type
            or "application/octet-stream"
        ),
    )

    values = campaign_attachment_update_values(
        filename=validated["filename"],
        content=validated["content"],
        content_type=validated["content_type"],
    )

    db.execute(
        text("""
            UPDATE campaigns
            SET attachment_name = :attachment_name,
                attachment_content = :attachment_content,
                attachment_mime_type = :attachment_mime_type,
                attachment_size = :attachment_size,
                attachment_url = NULL,
                updated_at = NOW()
            WHERE id = :id
        """),
        {
            **values,
            "id": campaign_id,
        },
    )

    db.commit()

    return {
        "campaign_id": campaign_id,
        "attachment_name": values["attachment_name"],
        "attachment_size": values["attachment_size"],
        "attachment_mime_type": values["attachment_mime_type"],
    }


@router.delete("/campaigns/{campaign_id}/attachment")
def delete_campaign_attachment(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    campaign = db.execute(
        text("""
            SELECT id, status
            FROM campaigns
            WHERE id = :id
        """),
        {"id": campaign_id},
    ).fetchone()

    if not campaign:
        raise HTTPException(404, "Campaign not found")

    ensure_campaign_attachment_editable(campaign.status)

    db.execute(
        text("""
            UPDATE campaigns
            SET attachment_name = NULL,
                attachment_content = NULL,
                attachment_mime_type = NULL,
                attachment_size = NULL,
                attachment_url = NULL,
                updated_at = NOW()
            WHERE id = :id
        """),
        {"id": campaign_id},
    )

    db.commit()

    return {
        "campaign_id": campaign_id,
        "attachment_name": None,
        "attachment_size": None,
        "attachment_mime_type": None,
    }


# ── Campaign endpoints ─────────────────────────────────────────────

@router.post("/campaigns")
def create_campaign(
    body: CampaignCreate,
    db:   Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Create a draft campaign."""
    if body.recipient_type not in ("leads", "mailing_list", "csv_upload"):
        raise HTTPException(400, "recipient_type must be leads, mailing_list, or csv_upload")

    if body.recipient_type == "mailing_list" and not body.mailing_list_id:
        raise HTTPException(400, "mailing_list_id required for mailing_list recipient type")

    row = db.execute(text("""
        INSERT INTO campaigns (
            name, subject, body, html_body, communication_type,
            sender_name, sender_email,
            reply_to, recipient_type, mailing_list_id,
            recipient_filter, status, created_by
        ) VALUES (
            :name, :subject, :body, :html_body, :communication_type,
            :sender_name, :sender_email,
            :reply_to, :recipient_type, :mailing_list_id,
            :recipient_filter, 'draft', :created_by
        )
        RETURNING id, name
    """), {
        "name":             body.name,
        "subject":          body.subject,
        "body":             body.body,
        "html_body":          body.html_body,
        "communication_type": body.communication_type,
        "sender_name":      body.sender_name,
        "sender_email":     body.sender_email,
        "reply_to":         body.reply_to,
        "recipient_type":   body.recipient_type,
        "mailing_list_id":  body.mailing_list_id,
        "recipient_filter": _serialize_recipient_filter(body.recipient_filter),
        "created_by":       user.name,
    }).fetchone()
    db.commit()

    return {"id": row.id, "name": row.name, "status": "draft"}

@router.post("/campaigns/{campaign_id}/recipients")
def upload_campaign_recipients(
    campaign_id: int,
    body: CampaignRecipientUpload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    campaign = db.execute(
        text("""
            SELECT id, recipient_type, status
            FROM campaigns
            WHERE id = :id
        """),
        {"id": campaign_id},
    ).fetchone()

    if not campaign:
        raise HTTPException(404, "Campaign not found")

    if campaign.recipient_type != "csv_upload":
        raise HTTPException(
            400,
            "Recipient upload is only available for csv_upload campaigns",
        )

    if campaign.status != "draft":
        raise HTTPException(
            400,
            "CSV recipients can only be uploaded to draft campaigns",
        )

    valid = []
    seen = set()
    invalid = 0
    duplicates = 0

    for recipient in body.recipients:
        email = str(
            recipient.get("email", "")
        ).strip().lower()

        name = str(
            recipient.get("name", "")
        ).strip() or None

        if not is_valid_email(email):
            invalid += 1
            continue

        if email in seen:
            duplicates += 1
            continue

        seen.add(email)

        valid.append({
            "email": email,
            "name": name,
        })

    db.execute(
        text("""
            DELETE FROM campaign_recipients
            WHERE campaign_id = :campaign_id
        """),
        {"campaign_id": campaign_id},
    )

    for recipient in valid:
        db.execute(
            text("""
                INSERT INTO campaign_recipients
                    (campaign_id, email, name, status)
                VALUES
                    (:campaign_id, :email, :name, 'pending')
            """),
            {
                "campaign_id": campaign_id,
                "email": recipient["email"],
                "name": recipient["name"],
            },
        )

    db.commit()

    return {
        "uploaded": len(body.recipients),
        "valid": len(valid),
        "invalid": invalid,
        "duplicates": duplicates,
    }


@router.patch("/campaigns/{campaign_id}")
def update_campaign(
    campaign_id: int,
    body: CampaignUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    campaign = db.execute(
        text("""
            SELECT id, status
            FROM campaigns
            WHERE id = :id
        """),
        {"id": campaign_id},
    ).fetchone()

    if not campaign:
        raise HTTPException(404, "Campaign not found")

    if campaign.status not in ("draft", "reviewed"):
        raise HTTPException(
            400,
            f"Cannot edit campaign with status '{campaign.status}'",
        )

    updates = body.model_dump(exclude_unset=True)

    if not updates:
        return {
            "id": campaign_id,
            "status": campaign.status,
        }

    assignments = ", ".join(
        f"{field} = :{field}"
        for field in updates
    )

    db.execute(
        text(f"""
            UPDATE campaigns
            SET {assignments},
                updated_at = NOW()
            WHERE id = :id
        """),
        {
            **updates,
            "id": campaign_id,
        },
    )

    db.commit()

    return {
        "id": campaign_id,
        "status": campaign.status,
        "message": "Campaign updated",
    }

@router.get("/campaigns")
def get_campaigns(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    rows = db.execute(text("""
        SELECT
            c.id,
            c.name,
            c.subject,
            c.status,
            c.recipient_type,
            c.communication_type,
            c.sender_email,
            c.total_recipients,
            c.sent_count,

            COALESCE(metrics.delivered_count, 0) AS delivered_count,
            COALESCE(metrics.opened_count, 0) AS opened_count,
            COALESCE(metrics.clicked_count, 0) AS clicked_count,
            COALESCE(metrics.bounced_count, 0) AS bounced_count,
            COALESCE(metrics.failed_count, c.failed_count, 0) AS failed_count,

            c.created_by,
            c.created_at,
            c.finished_at

        FROM campaigns c

        LEFT JOIN (
            SELECT
                campaign_id,

                COUNT(*) FILTER (
                    WHERE delivered_at IS NOT NULL
                ) AS delivered_count,

                COUNT(*) FILTER (
                    WHERE opened_at IS NOT NULL
                ) AS opened_count,

                COUNT(*) FILTER (
                    WHERE clicked_at IS NOT NULL
                ) AS clicked_count,

                COUNT(*) FILTER (
                    WHERE bounced_at IS NOT NULL
                ) AS bounced_count,

                COUNT(*) FILTER (
                    WHERE failed_at IS NOT NULL
                       OR status = 'failed'
                ) AS failed_count

            FROM campaign_recipients
            GROUP BY campaign_id
        ) metrics
            ON metrics.campaign_id = c.id

        ORDER BY c.created_at DESC
        LIMIT 50
    """)).fetchall()

    return [
        {
            "id": r.id,
            "name": r.name,
            "subject": r.subject,
            "status": r.status,
            "recipient_type": r.recipient_type,
            "communication_type": r.communication_type,
            "sender_email": r.sender_email,

            "total_recipients": r.total_recipients or 0,
            "sent_count": r.sent_count or 0,

            "delivered_count": r.delivered_count or 0,
            "opened_count": r.opened_count or 0,
            "clicked_count": r.clicked_count or 0,
            "bounced_count": r.bounced_count or 0,
            "failed_count": r.failed_count or 0,

            "created_by": r.created_by,

            "created_at": (
                r.created_at.isoformat()
                if r.created_at else None
            ),

            "finished_at": (
                r.finished_at.isoformat()
                if r.finished_at else None
            ),
        }
        for r in rows
    ]

@router.get("/campaigns/{campaign_id}")
def get_campaign(
    campaign_id: int,
    db:          Session = Depends(get_db),
    user:        CurrentUser = Depends(get_current_user),
):
    row = db.execute(text("""
        SELECT * FROM campaigns WHERE id = :id
    """), {"id": campaign_id}).fetchone()

    if not row:
        raise HTTPException(404, "Campaign not found")

    # Recipient stats
    stats = db.execute(text("""
        SELECT status, COUNT(*) as count
        FROM campaign_recipients
        WHERE campaign_id = :id
        GROUP BY status
    """), {"id": campaign_id}).fetchall()

    return {
        "id":               row.id,
        "name":             row.name,
        "subject":          row.subject,
        "body":             row.body,
        "html_body":        row.html_body,
        "sender_name":      row.sender_name,
        "sender_email":     row.sender_email,
        "reply_to":         row.reply_to,
        "status":           row.status,
        "recipient_type":   row.recipient_type,
        "communication_type": row.communication_type,
        "total_recipients": row.total_recipients,
        "sent_count":       row.sent_count,
        "failed_count":     row.failed_count,
        "created_by":       row.created_by,
        "created_at":       row.created_at.isoformat() if row.created_at else None,
        "finished_at":      row.finished_at.isoformat() if row.finished_at else None,
        "recipient_stats":  {r.status: r.count for r in stats},
    }

@router.get("/campaigns/{campaign_id}/performance")
def get_campaign_performance(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    campaign = db.execute(
        text("""
            SELECT
                id,
                name,
                subject,
                status,
                recipient_type,
                communication_type,
                created_at,
                finished_at
            FROM campaigns
            WHERE id = :id
        """),
        {"id": campaign_id},
    ).fetchone()

    if not campaign:
        raise HTTPException(
            status_code=404,
            detail="Campaign not found",
        )

    summary = db.execute(
        text("""
            SELECT
                COUNT(*) AS recipients,

                COUNT(*) FILTER (
                    WHERE sent_at IS NOT NULL
                ) AS sent,

                COUNT(*) FILTER (
                    WHERE delivered_at IS NOT NULL
                ) AS delivered,

                COUNT(*) FILTER (
                    WHERE opened_at IS NOT NULL
                ) AS opened,

                COUNT(*) FILTER (
                    WHERE clicked_at IS NOT NULL
                ) AS clicked,

                COUNT(*) FILTER (
                    WHERE bounced_at IS NOT NULL
                ) AS bounced,

                COUNT(*) FILTER (
                    WHERE failed_at IS NOT NULL
                       OR status = 'failed'
                ) AS failed,

                COALESCE(
                    SUM(open_count),
                    0
                ) AS open_events,

                COALESCE(
                    SUM(click_count),
                    0
                ) AS click_events

            FROM campaign_recipients
            WHERE campaign_id = :id
        """),
        {"id": campaign_id},
    ).fetchone()

    recipients = db.execute(
        text("""
            SELECT
                email,
                name,
                status,
                resend_id,
                sent_at,
                delivered_at,
                opened_at,
                clicked_at,
                bounced_at,
                failed_at,
                open_count,
                click_count,
                bounce_reason,
                error,
                last_event_at

            FROM campaign_recipients
            WHERE campaign_id = :id

            ORDER BY created_at DESC
        """),
        {"id": campaign_id},
    ).fetchall()

    recipient_count = summary.recipients or 0
    delivered_count = summary.delivered or 0
    opened_count = summary.opened or 0
    clicked_count = summary.clicked or 0

    delivery_rate = (
        round(
            delivered_count / recipient_count * 100,
            1,
        )
        if recipient_count
        else 0.0
    )

    open_rate = (
        round(
            opened_count / delivered_count * 100,
            1,
        )
        if delivered_count
        else 0.0
    )

    click_rate = (
        round(
            clicked_count / delivered_count * 100,
            1,
        )
        if delivered_count
        else 0.0
    )

    def serialize_date(value):
        return value.isoformat() if value else None

    return {
        "campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "subject": campaign.subject,
            "status": campaign.status,
            "recipient_type": campaign.recipient_type,
            "communication_type": campaign.communication_type,
            "created_at": serialize_date(
                campaign.created_at
            ),
            "finished_at": serialize_date(
                campaign.finished_at
            ),
        },

        "summary": {
            "recipients": recipient_count,
            "sent": summary.sent or 0,
            "delivered": delivered_count,
            "opened": opened_count,
            "clicked": clicked_count,
            "bounced": summary.bounced or 0,
            "failed": summary.failed or 0,

            "open_events": summary.open_events or 0,
            "click_events": summary.click_events or 0,

            "delivery_rate": delivery_rate,
            "open_rate": open_rate,
            "click_rate": click_rate,
        },

        "recipients": [
            {
                "email": r.email,
                "name": r.name,
                "status": r.status,
                "resend_id": r.resend_id,

                "sent_at": serialize_date(r.sent_at),
                "delivered_at": serialize_date(
                    r.delivered_at
                ),
                "opened_at": serialize_date(
                    r.opened_at
                ),
                "clicked_at": serialize_date(
                    r.clicked_at
                ),
                "bounced_at": serialize_date(
                    r.bounced_at
                ),
                "failed_at": serialize_date(
                    r.failed_at
                ),

                "open_count": r.open_count or 0,
                "click_count": r.click_count or 0,

                "bounce_reason": r.bounce_reason,
                "error": r.error,

                "last_event_at": serialize_date(
                    r.last_event_at
                ),
            }
            for r in recipients
        ],
    }


def _lead_filter_query(recipient_filter):
    """Build the shared WHERE clause for filtered lead campaigns."""
    filters = ["email IS NOT NULL", "email != ''"]
    params = {}

    rf = (
        _parse_recipient_filter(recipient_filter)
        if recipient_filter
        else {}
    )

    if rf.get("lead_type"):
        filters.append("lead_type = :lead_type")
        params["lead_type"] = rf["lead_type"]

    if rf.get("area"):
        filters.append("area ILIKE :area")
        params["area"] = f"%{rf['area']}%"

    if rf.get("status"):
        filters.append("status = :status")
        params["status"] = rf["status"]

    if rf.get("ai_score"):
        filters.append("ai_score = :ai_score")
        params["ai_score"] = rf["ai_score"]

    return " AND ".join(filters), params


def _resolve_recipients(campaign, db: Session) -> List[dict]:
    """
    Resolve recipients based on campaign type.
    Returns list of {email, name, lead_id}.
    Removes unsubscribed and invalid emails.
    """
    recipients = []

    if campaign.recipient_type == "mailing_list":
        rows = db.execute(text("""
            SELECT email, name, lead_id FROM mailing_list_contacts
            WHERE list_id = :list_id AND unsubscribed = FALSE
        """), {"list_id": campaign.mailing_list_id}).fetchall()
        recipients = [{"email": r.email, "name": r.name, "lead_id": r.lead_id}
                      for r in rows]

    elif campaign.recipient_type == "leads":
        where, params = _lead_filter_query(
            campaign.recipient_filter
        )

        rows = db.execute(text(f"""
            SELECT id, name, email
            FROM leads
            WHERE {where}
        """), params).fetchall()

        recipients = [
            {
                "email": r.email,
                "name": r.name,
                "lead_id": r.id,
            }
            for r in rows
        ]

    elif campaign.recipient_type == "csv_upload":
        rows = db.execute(text("""
            SELECT email, name, lead_id FROM campaign_recipients
            WHERE campaign_id = :id AND status IN ('reviewed', 'pending')
        """), {"id": campaign.id}).fetchall()
        recipients = [{"email": r.email, "name": r.name, "lead_id": r.lead_id}
                      for r in rows]

    # Remove unsubscribed and invalid
    clean = []
    seen  = set()
    for r in recipients:
        email = r["email"].strip().lower()
        if not is_valid_email(email):
            continue
        if email in seen:
            continue
        if is_unsubscribed(db, email):
            continue
        seen.add(email)
        r["email"] = email
        clean.append(r)

    return clean


async def _send_campaign_emails(campaign_id: int, db_url: str):
    """Background task — sends emails to all recipients."""
    import psycopg2

    conn = psycopg2.connect(db_url)
    cur  = conn.cursor()

    try:
        # Fetch campaign
        cur.execute("SELECT * FROM campaigns WHERE id = %s", (campaign_id,))
        cols = [d[0] for d in cur.description]
        row  = cur.fetchone()
        if not row:
            return

        campaign = dict(zip(cols, row))

        # Update status to sending
        cur.execute("""
            UPDATE campaigns SET status = 'sending', started_at = NOW()
            WHERE id = %s
        """, (campaign_id,))
        conn.commit()

        # Get recipients from campaign_recipients table
        cur.execute("""
            SELECT email, name, lead_id FROM campaign_recipients
            WHERE campaign_id = %s AND status IN ('reviewed', 'pending')
        """, (campaign_id,))
        recipients = [
            {"email": r[0], "name": r[1], "lead_id": r[2]}
            for r in cur.fetchall()
        ]

        sent_count   = 0
        failed_count = 0

        for r in recipients:
            unsubscribe_url = (
                f"{_get_app_url()}/unsubscribe?email={r['email']}"
            )

            context = {
                "contact_name": r["name"] or "Property Manager",
                "company_name": r["name"] or "your company",
                "rep_name":     campaign["created_by"],
                "rep_email":    campaign["sender_email"],
                "area":         "",
                "unsubscribe_url": unsubscribe_url,
            }

            personalised_body = personalise(
                campaign["body"],
                context,
            )
            personalised_subject = personalise(
                campaign["subject"],
                context,
            )
            personalised_html = (
                personalise(campaign["html_body"], context)
                if campaign.get("html_body")
                else None
            )

            try:
                result = await send_via_resend(
                    to_email   = r["email"],
                    to_name    = r["name"] or "",
                    from_email = campaign["sender_email"],
                    from_name  = campaign["sender_name"],
                    subject    = personalised_subject,
                    body       = personalised_body,
                    html_body  = personalised_html,
                    reply_to   = campaign.get("reply_to"),
                    append_unsubscribe_footer=(
                        campaign.get("communication_type")
                        != "newsletter"
                    ),
                    **campaign_attachment_send_kwargs(campaign),
                )

                cur.execute("""
                    UPDATE campaign_recipients SET
                        status    = 'sent',
                        resend_id = %s,
                        sent_at   = NOW()
                    WHERE campaign_id = %s AND email = %s
                """, (result.get("id"), campaign_id, r["email"]))
                sent_count += 1

            except Exception as e:
                cur.execute("""
                    UPDATE campaign_recipients SET
                        status = 'failed',
                        error  = %s
                    WHERE campaign_id = %s AND email = %s
                """, (str(e)[:200], campaign_id, r["email"]))
                failed_count += 1

            conn.commit()

        # Mark campaign complete using the actual delivery outcome.
        final_status = _delivery_outcome(
            sent_count=sent_count,
            failed_count=failed_count,
        )

        cur.execute("""
            UPDATE campaigns SET
                status       = %s,
                finished_at  = NOW(),
                sent_count   = %s,
                failed_count = %s
            WHERE id = %s
        """, (
            final_status,
            sent_count,
            failed_count,
            campaign_id,
        ))
        conn.commit()

        logger.info(
            f"Campaign {campaign_id} complete — "
            f"sent:{sent_count} failed:{failed_count}"
        )

    except Exception as e:
        logger.error(f"Campaign {campaign_id} failed: {e}")
        cur.execute("""
            UPDATE campaigns SET status = 'failed'
            WHERE id = %s
        """, (campaign_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()




@router.post("/campaigns/{campaign_id}/review")
def review_campaign(
    campaign_id: int,
    db:          Session = Depends(get_db),
    user:        CurrentUser = Depends(get_current_user),
):
    """
    STEP 1 of 2 — Resolve and lock recipients for review.

    Resolves recipients RIGHT NOW based on campaign settings,
    saves them to campaign_recipients, and locks the campaign.
    The user must review this list before sending.

    After this call the recipient list is FROZEN.
    Sending will use exactly these rows — no recalculation.
    """
    campaign = db.execute(text("""
        SELECT * FROM campaigns WHERE id = :id
    """), {"id": campaign_id}).fetchone()

    if not campaign:
        raise HTTPException(404, "Campaign not found")

    if campaign.status not in ("draft", "failed"):
        raise HTTPException(400,
            f"Campaign is '{campaign.status}' — only draft or failed campaigns can be reviewed. "
            f"To start over, create a new campaign."
        )

    # Resolve recipients now and freeze them
    recipients = _resolve_recipients(campaign, db)

    # Count what was excluded
    # Get raw count before filtering
    raw_count = 0
    if campaign.recipient_type == "mailing_list" and campaign.mailing_list_id:
        raw_count = db.execute(text("""
            SELECT COUNT(*) FROM mailing_list_contacts
            WHERE list_id = :list_id
        """), {"list_id": campaign.mailing_list_id}).scalar()
    elif campaign.recipient_type == "leads":
        where, params = _lead_filter_query(
            campaign.recipient_filter
        )

        raw_count = db.execute(
            text(f"""
                SELECT COUNT(*)
                FROM leads
                WHERE {where}
            """),
            params,
        ).scalar()
    elif campaign.recipient_type == "csv_upload":
        raw_count = db.execute(text("""
            SELECT COUNT(*) FROM campaign_recipients
            WHERE campaign_id = :id
        """), {"id": campaign_id}).scalar()

    valid_count       = len(recipients)
    excluded_count    = raw_count - valid_count

    # Wipe any previous recipients and save the resolved list
    db.execute(text("""
        DELETE FROM campaign_recipients WHERE campaign_id = :id
    """), {"id": campaign_id})

    for r in recipients:
        db.execute(text("""
            INSERT INTO campaign_recipients
                (campaign_id, email, name, lead_id, status)
            VALUES (:campaign_id, :email, :name, :lead_id, 'reviewed')
        """), {
            "campaign_id": campaign_id,
            "email":       r["email"],
            "name":        r.get("name"),
            "lead_id":     r.get("lead_id"),
        })

    # Lock campaign as reviewed
    db.execute(text("""
        UPDATE campaigns SET
            status           = 'reviewed',
            total_recipients = :count,
            updated_at       = NOW()
        WHERE id = :id
    """), {"count": valid_count, "id": campaign_id})
    db.commit()

    # Check for unsubscribes excluded
    unsub_count = db.execute(text("""
        SELECT COUNT(*) FROM unsubscribes
    """)).scalar()

    return {
        "campaign_id":     campaign_id,
        "campaign_name":   campaign.name,
        "subject":         campaign.subject,
        "sender":          f"{campaign.sender_name} <{campaign.sender_email}>",
        "status":          "reviewed",
        "recipient_summary": {
            "will_receive":    valid_count,
            "excluded_total":  excluded_count,
            "breakdown": {
                "invalid_email":  "Emails that failed format validation",
                "duplicates":     "Same email appearing more than once",
                "unsubscribed":   f"{unsub_count} addresses on global unsubscribe list",
            }
        },
        "recipients": [
            {
                "email": r["email"],
                "name":  r.get("name") or "—",
            }
            for r in recipients
        ],
        "confirmation_required": True,
        "next_step": (
            f"Review the {valid_count} recipients above. "
            f"If correct, call POST /api/comms/campaigns/{campaign_id}/confirm-send "
            f"to begin delivery. This list is now locked."
        ),
    }


@router.post("/campaigns/{campaign_id}/confirm-send")
async def confirm_send(
    campaign_id:      int,
    background_tasks: BackgroundTasks,
    db:               Session = Depends(get_db),
    user:             CurrentUser = Depends(get_current_user),
):
    """
    STEP 2 of 2 — Confirm and send to the reviewed recipient list.

    Only works after /review has been called.
    Sends to EXACTLY the recipients saved during review.
    No recalculation — what was reviewed is what gets sent.
    """
    campaign = db.execute(text("""
        SELECT * FROM campaigns WHERE id = :id
    """), {"id": campaign_id}).fetchone()

    if not campaign:
        raise HTTPException(404, "Campaign not found")

    if campaign.status != "reviewed":
        raise HTTPException(400,
            f"Campaign must be in 'reviewed' status before sending. "
            f"Current status: '{campaign.status}'. "
            f"Call /review first."
        )

    # Count locked recipients
    recipient_count = db.execute(text("""
        SELECT COUNT(*) FROM campaign_recipients
        WHERE campaign_id = :id AND status = 'reviewed'
    """), {"id": campaign_id}).scalar()

    if recipient_count == 0:
        raise HTTPException(400,
            "No reviewed recipients found. Call /review first."
        )

    # Mark as sending immediately so no second confirm can slip through
    db.execute(text("""
        UPDATE campaigns SET
            status     = 'sending',
            started_at = NOW(),
            updated_at = NOW()
        WHERE id = :id AND status = 'reviewed'
    """), {"id": campaign_id})
    db.commit()

    # Get DB URL for background task
    from app.config import settings
    db_url = settings.database_url

    background_tasks.add_task(
        _send_campaign_emails, campaign_id, db_url
    )

    return {
        "campaign_id":      campaign_id,
        "status":           "sending",
        "total_recipients": recipient_count,
        "confirmed_by":     user.name,
        "message": (
            f"Confirmed by {user.name}. "
            f"Sending to {recipient_count} reviewed recipients in background. "
            f"Poll GET /api/comms/campaigns/{campaign_id} for progress."
        ),
    }


@router.post("/campaigns/{campaign_id}/send")
async def send_campaign(
    campaign_id:      int,
    background_tasks: BackgroundTasks,
    db:               Session = Depends(get_db),
    user:             CurrentUser = Depends(get_current_user),
):
    """
    Resolve recipients and kick off sending in background.
    Returns immediately with recipient count.
    """
    campaign = db.execute(text("""
        SELECT * FROM campaigns WHERE id = :id
    """), {"id": campaign_id}).fetchone()

    if not campaign:
        raise HTTPException(404, "Campaign not found")
    if campaign.status not in ("draft", "failed"):
        raise HTTPException(400, f"Cannot send campaign with status '{campaign.status}'")

    # Resolve recipients
    recipients = _resolve_recipients(campaign, db)
    if not recipients:
        raise HTTPException(400, "No valid recipients found")

    # Save recipients to campaign_recipients
    db.execute(text("""
        DELETE FROM campaign_recipients WHERE campaign_id = :id
    """), {"id": campaign_id})

    for r in recipients:
        db.execute(text("""
            INSERT INTO campaign_recipients
                (campaign_id, email, name, lead_id, status)
            VALUES (:campaign_id, :email, :name, :lead_id, 'pending')
            ON CONFLICT DO NOTHING
        """), {
            "campaign_id": campaign_id,
            "email":       r["email"],
            "name":        r.get("name"),
            "lead_id":     r.get("lead_id"),
        })

    # Update total count
    db.execute(text("""
        UPDATE campaigns SET
            total_recipients = :count,
            status           = 'sending'
        WHERE id = :id
    """), {"count": len(recipients), "id": campaign_id})
    db.commit()

    # Get DB URL for background task
    from app.config import settings
    db_url = settings.database_url

    # Send in background
    background_tasks.add_task(
        _send_campaign_emails, campaign_id, db_url
    )

    return {
        "campaign_id":      campaign_id,
        "total_recipients": len(recipients),
        "status":           "sending",
        "message": (
            f"Sending to {len(recipients)} recipients in background. "
            f"Poll GET /api/comms/campaigns/{campaign_id} for progress."
        ),
    }


@router.get("/campaigns/{campaign_id}/recipients")
def get_recipients(
    campaign_id: int,
    status:      Optional[str] = None,
    page:        int = 1,
    limit:       int = 50,
    db:          Session = Depends(get_db),
    user:        CurrentUser = Depends(get_current_user),
):
    """Recipient list with delivery status."""
    filters = ["campaign_id = :campaign_id"]
    params  = {"campaign_id": campaign_id}

    if status:
        filters.append("status = :status")
        params["status"] = status

    where  = " AND ".join(filters)
    offset = (page - 1) * limit

    total = db.execute(
        text(f"SELECT COUNT(*) FROM campaign_recipients WHERE {where}"), params
    ).scalar()

    rows = db.execute(text(f"""
        SELECT email, name, status, resend_id, sent_at, error, created_at
        FROM campaign_recipients
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """), {**params, "limit": limit, "offset": offset}).fetchall()

    return {
        "total": total,
        "page":  page,
        "pages": -(-total // limit) if total else 0,
        "data": [
            {
                "email":     r.email,
                "name":      r.name,
                "status":    r.status,
                "resend_id": r.resend_id,
                "sent_at":   r.sent_at.isoformat() if r.sent_at else None,
                "error":     r.error,
            }
            for r in rows
        ],
    }


@router.post("/campaigns/{campaign_id}/upload-csv")
async def upload_csv_recipients(
    campaign_id: int,
    file:        UploadFile = File(...),
    db:          Session = Depends(get_db),
    user:        CurrentUser = Depends(get_current_user),
):
    """Upload CSV/Excel file as recipients for a campaign."""
    import io, csv
    import openpyxl

    content  = await file.read()
    filename = file.filename.lower()

    rows = []
    if filename.endswith(".csv"):
        try:
            text_content = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text_content = content.decode("latin-1")

        first_line = text_content.split("\n")[0]
        delimiter  = "\t" if "\t" in first_line else ","
        reader     = csv.DictReader(io.StringIO(text_content), delimiter=delimiter)
        rows       = list(reader)

    elif filename.endswith((".xlsx", ".xls")):
        wb   = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        ws   = wb.active
        data = list(ws.iter_rows(values_only=True))
        if data:
            headers = [str(h).strip().lower() if h else "" for h in data[0]]
            for row in data[1:]:
                rows.append({headers[i]: str(cell).strip() if cell else ""
                             for i, cell in enumerate(row)})
    else:
        raise HTTPException(400, "Only CSV or Excel files supported")

    # Column aliases
    EMAIL_COLS = ["email", "email address", "e-mail"]
    NAME_COLS  = ["name", "company name", "apartment name/company name",
                  "business name", "contact name"]

    added   = 0
    skipped = 0
    invalid = []

    for row in rows:
        # Find email
        email = ""
        for col in EMAIL_COLS:
            val = row.get(col, "").strip().lower()
            if val:
                email = val
                break

        # Find name
        name = ""
        for col in NAME_COLS:
            val = row.get(col, "").strip()
            if val:
                name = val
                break

        if not email or not is_valid_email(email):
            if email:
                invalid.append(email)
            continue

        if is_unsubscribed(db, email):
            skipped += 1
            continue

        try:
            db.execute(text("""
                INSERT INTO campaign_recipients
                    (campaign_id, email, name, status)
                VALUES (:campaign_id, :email, :name, 'pending')
                ON CONFLICT DO NOTHING
            """), {
                "campaign_id": campaign_id,
                "email":       email,
                "name":        name or None,
            })
            added += 1
        except Exception:
            skipped += 1

    db.commit()
    return {
        "added":   added,
        "skipped": skipped,
        "invalid": invalid,
        "message": f"{added} recipients added from {filename}",
    }


# ── Unsubscribe ───────────────────────────────────────────────────

@router.post("/unsubscribe")
def unsubscribe(
    body: UnsubscribeRequest,
    db:   Session = Depends(get_db),
):
    """Global unsubscribe — no auth required."""
    email = body.email.strip().lower()
    if not is_valid_email(email):
        raise HTTPException(400, "Invalid email address")

    db.execute(text("""
        INSERT INTO unsubscribes (email, reason)
        VALUES (:email, :reason)
        ON CONFLICT (email) DO NOTHING
    """), {"email": email, "reason": body.reason})

    # Also mark in mailing lists
    db.execute(text("""
        UPDATE mailing_list_contacts SET
            unsubscribed    = TRUE,
            unsubscribed_at = NOW()
        WHERE email = :email
    """), {"email": email})

    db.commit()
    return {"message": f"{email} has been unsubscribed successfully"}


@router.get("/unsubscribes")
def get_unsubscribes(
    db:   Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    rows = db.execute(text("""
        SELECT email, reason, unsubscribed_at
        FROM unsubscribes
        ORDER BY unsubscribed_at DESC
        LIMIT 100
    """)).fetchall()

    return [
        {
            "email":            r.email,
            "reason":           r.reason,
            "unsubscribed_at":  r.unsubscribed_at.isoformat() if r.unsubscribed_at else None,
        }
        for r in rows
    ]
