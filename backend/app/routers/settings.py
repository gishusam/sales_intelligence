"""
settings.py — System settings management
Currently: email templates + sender config

GET  /api/settings/email          — get all email settings
PUT  /api/settings/email          — update email settings
GET  /api/settings/email/templates — get templates formatted for email router
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, CurrentUser

router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = logging.getLogger(__name__)


# ── Schemas ───────────────────────────────────────────────────────

class TemplateUpdate(BaseModel):
    subject: str
    body:    str


class EmailSettingsUpdate(BaseModel):
    sender_name:         Optional[str] = None
    template_cold_1:     Optional[TemplateUpdate] = None
    template_cold_2:     Optional[TemplateUpdate] = None
    template_cold_3:     Optional[TemplateUpdate] = None
    template_cold_4:     Optional[TemplateUpdate] = None
    template_cold_5:     Optional[TemplateUpdate] = None
    template_followup:   Optional[TemplateUpdate] = None


# ── Helper ────────────────────────────────────────────────────────

def get_all_settings(db: Session) -> dict:
    """Fetch all settings as a key-value dict."""
    rows = db.execute(text("""
        SELECT key, value, description, updated_by, updated_at
        FROM email_settings ORDER BY key
    """)).fetchall()
    return {r.key: {
        "value":       r.value,
        "description": r.description,
        "updated_by":  r.updated_by,
        "updated_at":  r.updated_at.isoformat() if r.updated_at else None,
    } for r in rows}


def upsert_setting(db: Session, key: str, value: str, updated_by: str):
    db.execute(text("""
        INSERT INTO email_settings (key, value, updated_by, updated_at)
        VALUES (:key, :value, :updated_by, NOW())
        ON CONFLICT (key) DO UPDATE SET
            value      = EXCLUDED.value,
            updated_by = EXCLUDED.updated_by,
            updated_at = NOW()
    """), {"key": key, "value": value, "updated_by": updated_by})


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/email")
def get_email_settings(
    db:   Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Returns all email settings including templates.
    Used by the Settings page to populate the editor.
    """
    settings = get_all_settings(db)

    def get_val(key: str) -> str:
        return settings.get(key, {}).get("value", "")

    def get_meta(key: str) -> dict:
        s = settings.get(key, {})
        return {
            "updated_by": s.get("updated_by"),
            "updated_at": s.get("updated_at"),
        }

    return {
        "sender_name": {
            "value": get_val("sender_name"),
            **get_meta("sender_name"),
        },
        "templates": {
            "cold_1": {
                "subject": get_val("template_cold_1_subject"),
                "body":    get_val("template_cold_1_body"),
                "label":   "Template 1 — Request for Demo",
                **get_meta("template_cold_1_subject"),
            },
            "cold_2": {
                "subject": get_val("template_cold_2_subject"),
                "body":    get_val("template_cold_2_body"),
                "label":   "Template 2 — Smarter Solution",
                **get_meta("template_cold_2_subject"),
            },
            "cold_3": {
                "subject": get_val("template_cold_3_subject"),
                "body":    get_val("template_cold_3_body"),
                "label":   "Template 3 — Revolutionize",
                **get_meta("template_cold_3_subject"),
            },
            "cold_4": {
                "subject": get_val("template_cold_4_subject"),
                "body":    get_val("template_cold_4_body"),
                "label":   "Template 4 — Unlock Efficiency",
                **get_meta("template_cold_4_subject"),
            },
            "cold_5": {
                "subject": get_val("template_cold_5_subject"),
                "body":    get_val("template_cold_5_body"),
                "label":   "Template 5 — Elevate Experience",
                **get_meta("template_cold_5_subject"),
            },
            "followup": {
                "subject": get_val("template_followup_subject"),
                "body":    get_val("template_followup_body"),
                "label":   "Follow-up Template",
                **get_meta("template_followup_subject"),
            },
        },
        "placeholders": [
            "{contact_name}",
            "{company_name}",
            "{area}",
            "{rep_name}",
            "{rep_email}",
        ],
        "placeholder_help": {
            "{contact_name}": "Lead's contact person name",
            "{company_name}": "Company or building name",
            "{area}":         "Area/location of the lead",
            "{rep_name}":     "Sales rep's full name",
            "{rep_email}":    "Sales rep's email address",
        }
    }


@router.put("/email")
def update_email_settings(
    body: EmailSettingsUpdate,
    db:   Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Update email settings — any rep can edit.
    Only updates fields that are provided.
    """
    updates = 0

    if body.sender_name is not None:
        upsert_setting(db, "sender_name", body.sender_name, user.name)
        updates += 1

    template_map = {
        "template_cold_1":   body.template_cold_1,
        "template_cold_2":   body.template_cold_2,
        "template_cold_3":   body.template_cold_3,
        "template_cold_4":   body.template_cold_4,
        "template_cold_5":   body.template_cold_5,
        "template_followup": body.template_followup,
    }

    for key, tmpl in template_map.items():
        if tmpl is not None:
            if not tmpl.subject.strip():
                raise HTTPException(400, f"{key} subject cannot be empty")
            if not tmpl.body.strip():
                raise HTTPException(400, f"{key} body cannot be empty")
            if "{rep_name}" not in tmpl.body:
                raise HTTPException(400,
                    f"{key} body must contain {{rep_name}} placeholder"
                )
            upsert_setting(db, f"{key}_subject", tmpl.subject, user.name)
            upsert_setting(db, f"{key}_body",    tmpl.body,    user.name)
            updates += 2

    db.commit()

    return {
        "updated":    updates,
        "updated_by": user.name,
        "message":    f"{updates} settings updated successfully",
    }


@router.get("/email/templates")
def get_templates_for_email(
    db:   Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Returns templates in the format the email router expects.
    Called internally by the email preview endpoint.
    """
    settings = get_all_settings(db)

    def get_val(key: str) -> str:
        return settings.get(key, {}).get("value", "")

    return {
        "sender_name": get_val("sender_name"),
        "cold": {
            "template_1": {
                "subject": get_val("template_cold_1_subject"),
                "body":    get_val("template_cold_1_body"),
            },
            "template_2": {
                "subject": get_val("template_cold_2_subject"),
                "body":    get_val("template_cold_2_body"),
            },
            "template_3": {
                "subject": get_val("template_cold_3_subject"),
                "body":    get_val("template_cold_3_body"),
            },
            "template_4": {
                "subject": get_val("template_cold_4_subject"),
                "body":    get_val("template_cold_4_body"),
            },
            "template_5": {
                "subject": get_val("template_cold_5_subject"),
                "body":    get_val("template_cold_5_body"),
            },
        },
        "followup": {
            "subject": get_val("template_followup_subject"),
            "body":    get_val("template_followup_body"),
        },
    }
