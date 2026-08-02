"""
settings.py — Email template settings

GET /api/settings/email     — get both templates + sender name
PUT /api/settings/email     — update templates or sender name
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, CurrentUser

router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = logging.getLogger(__name__)


class TemplateUpdate(BaseModel):
    subject: str
    body:    str


class EmailSettingsUpdate(BaseModel):
    sender_name:       Optional[str]            = None
    template_cold:     Optional[TemplateUpdate] = None
    template_followup: Optional[TemplateUpdate] = None


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.execute(
        text("SELECT value FROM email_settings WHERE key = :key"),
        {"key": key}
    ).fetchone()
    return row.value if row else default


def upsert_setting(db: Session, key: str, value: str, updated_by: str):
    db.execute(text("""
        INSERT INTO email_settings (key, value, updated_by, updated_at)
        VALUES (:key, :value, :updated_by, NOW())
        ON CONFLICT (key) DO UPDATE SET
            value      = EXCLUDED.value,
            updated_by = EXCLUDED.updated_by,
            updated_at = NOW()
    """), {"key": key, "value": value, "updated_by": updated_by})


@router.get("/email")
def get_email_settings(
    db:   Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Returns both templates and sender name for the settings page."""
    return {
        "sender_name": get_setting(db, "sender_name", "Nyumba Zetu Sales"),
        "template_cold": {
            "subject": get_setting(db, "template_cold_subject",
                "Request for Demo Meeting — Nyumba Zetu Property Management"),
            "body": get_setting(db, "template_cold_body", ""),
            "label": "Cold Outreach Template",
        },
        "template_followup": {
            "subject": get_setting(db, "template_followup_subject",
                "Following Up — Nyumba Zetu Property Management"),
            "body": get_setting(db, "template_followup_body", ""),
            "label": "Follow-up Template",
        },
        "placeholders": {
            "{contact_name}": "Lead contact person name",
            "{company_name}": "Company or building name",
            "{area}":         "Area or location",
            "{rep_name}":     "Your full name",
            "{rep_email}":    "Your email address",
        }
    }


@router.put("/email")
def update_email_settings(
    body: EmailSettingsUpdate,
    db:   Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Update templates — any rep can edit."""
    updates = 0

    if body.sender_name is not None:
        upsert_setting(db, "sender_name", body.sender_name.strip(), user.name)
        updates += 1

    for key, tmpl in [
        ("template_cold",     body.template_cold),
        ("template_followup", body.template_followup),
    ]:
        if tmpl is not None:
            if not tmpl.subject.strip():
                raise HTTPException(400, f"{key} subject cannot be empty")
            if not tmpl.body.strip():
                raise HTTPException(400, f"{key} body cannot be empty")
            if "{rep_name}" not in tmpl.body:
                raise HTTPException(400,
                    f"{key} body must contain {{rep_name}}"
                )
            upsert_setting(db, f"{key}_subject", tmpl.subject, user.name)
            upsert_setting(db, f"{key}_body",    tmpl.body,    user.name)
            updates += 2

    db.commit()
    return {
        "updated":    updates,
        "updated_by": user.name,
        "message":    f"{updates} settings updated by {user.name}",
    }
