"""
notes.py — Lead notes + automatic LLM scoring via Groq (free tier)
When a rep saves a note, Groq/Llama3 scores the lead automatically.

"""

import os
import json
import logging
import httpx
from datetime import datetime, timezone, date, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, CurrentUser

router = APIRouter(prefix="/api", tags=["notes"])
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"

VALID_SCORES = {
    "LOW_HANGING_FRUIT", "WARM_PROSPECT",
    "EXECUTIVE_LEAD", "NURTURE", "NOT_QUALIFIED"
}

SCORE_DISPLAY = {
    "LOW_HANGING_FRUIT": "Low Hanging Fruit",
    "WARM_PROSPECT":     "Warm Prospect",
    "EXECUTIVE_LEAD":    "Executive Lead",
    "NURTURE":           "Nurture",
    "NOT_QUALIFIED":     "Not Qualified",
}


# ── Schemas ───────────────────────────────────────────────────────

class NoteCreate(BaseModel):
    note: str
    created_by: Optional[str] = None


class NoteResponse(BaseModel):
    id: int
    lead_id: int
    note: str
    created_by: Optional[str]
    ai_score: Optional[str]
    ai_score_reason: Optional[str]
    follow_up_days: Optional[int]
    signals: Optional[List[str]]
    created_at: str


# ── LLM Scoring ───────────────────────────────────────────────────

def build_prompt(lead: dict, note: str) -> str:
    return f"""You are a sales intelligence assistant for Nyumba Zetu,
a property management software company in Nairobi, Kenya.
Our software helps property managers and landlords track rent,
manage tenants, handle maintenance, and automate communication.

Lead context:
- Name: {lead.get('name', 'Unknown')}
- Type: {lead.get('lead_type', 'Unknown')}
- Area: {lead.get('area', 'Unknown')}
- Current Score: {lead.get('score', 0)}
- Phone: {'Yes' if lead.get('phone') else 'No'}
- Website: {'Yes' if lead.get('website') else 'No'}

Sales rep note:
\"{note}\"

Based on this note, classify this lead as exactly one of:
- LOW_HANGING_FRUIT: Decision maker reached, clear pain point, ready to buy soon
- WARM_PROSPECT: Interested but has blockers (needs approval, timing, budget concerns)
- EXECUTIVE_LEAD: Large portfolio or enterprise account, needs senior rep attention
- NURTURE: Not ready now, follow up in 30+ days, keep warm
- NOT_QUALIFIED: Wrong fit, no budget, not a property manager/owner

Return ONLY valid JSON, no other text:
{{
  "score": "ONE_OF_THE_FIVE_OPTIONS",
  "reason": "One sentence explaining why based on the note",
  "follow_up_days": 7,
  "signals": ["signal1", "signal2"]
}}

Signals should be short tags like: excel_user, owner_absent, large_portfolio,
price_sensitive, ready_to_buy, needs_demo, competitor_user, no_system, interested"""


async def score_with_llm(lead: dict, note: str) -> dict:
    """Call Groq API to score a lead based on the rep's note."""
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not set — returning default score")
        return {
            "score":          "WARM_PROSPECT",
            "reason":         "AI scoring unavailable — GROQ_API_KEY not configured",
            "follow_up_days": 7,
            "signals":        [],
        }

    prompt = build_prompt(lead, note)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "reasoning_effort": "low",
                "include_reasoning": False,
                "response_format": {"type": "json_object"},
                "max_completion_tokens": 1024,
                "temperature": 0.1,
            }
        )
        resp.raise_for_status()
        data = resp.json()

    raw = data["choices"][0]["message"]["content"].strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    result = json.loads(raw.strip())

    # Validate score — fall back to WARM_PROSPECT if invalid
    if result.get("score") not in VALID_SCORES:
        result["score"] = "WARM_PROSPECT"

    return result


# ── Endpoints ─────────────────────────────────────────────────────

@router.post("/leads/{lead_id}/notes")
async def create_note(
    lead_id: int,
    body: NoteCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Save a note and automatically score the lead with Groq/Llama3.
    The score is written back to both lead_notes and the leads table.
    """
    # Fetch lead context for the LLM
    lead_row = db.execute(text("""
        SELECT id, name, lead_type, area,
               score, phone, website, ai_score
        FROM leads WHERE id = :id
    """), {"id": lead_id}).fetchone()

    if not lead_row:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead = dict(lead_row._mapping)
    created_by = body.created_by or user.name

    # Call Groq — async so it doesn't block the request
    try:
        ai_result = await score_with_llm(lead, body.note)
    except Exception as e:
        logger.error(f"Groq scoring failed for lead {lead_id}: {e}")
        ai_result = {
            "score":          None,
            "reason":         "AI scoring unavailable — note saved but lead was not re-scored",
            "follow_up_days": None,
            "signals":        [],
        }

    follow_up_days = ai_result.get("follow_up_days")
    follow_up_date = (
        datetime.now(timezone.utc).date() + timedelta(days=follow_up_days)
        if follow_up_days is not None
        else None
    )

    # Save note with AI score to lead_notes table
    try:
        
        note_row = db.execute(text("""
            INSERT INTO lead_notes (
                lead_id, note, created_by,
                ai_score, ai_score_reason,
                follow_up_days, signals, created_at
            ) VALUES (
                :lead_id, :note, :created_by,
                :ai_score, :ai_score_reason,
                :follow_up_days, :signals, NOW()
            )
            RETURNING id, created_at
        """), {
            "lead_id":         lead_id,
            "note":            body.note,
            "created_by":      created_by,
            "ai_score":        ai_result.get("score"),
            "ai_score_reason": ai_result.get("reason"),
            "follow_up_days":  follow_up_days,
            "signals":         ai_result.get("signals", []),
        }).fetchone()
        db.commit()

        # Update lead's AI score + contact tracking
        db.execute(text("""
            UPDATE leads SET
                ai_score         = COALESCE(:ai_score, ai_score),
                ai_score_reason  = COALESCE(:ai_score_reason, ai_score_reason),
                ai_scored_at     = CASE
                    WHEN :ai_score IS NOT NULL THEN NOW()
                    ELSE ai_scored_at
                END,
                follow_up_date   = COALESCE(:follow_up_date, follow_up_date),
                last_contacted   = NOW(),
                contact_attempts = COALESCE(contact_attempts, 0) + 1,
                updated_at       = NOW()
            WHERE id = :id
        """), {
            "id":              lead_id,
            "ai_score":        ai_result.get("score"),
            "ai_score_reason": (
                ai_result.get("reason")
                if ai_result.get("score") is not None
                else None
            ),
            "follow_up_date":  follow_up_date,
        })
    
        db.commit()

    except Exception:
        db.rollback()
        raise    

    logger.info(
        f"Lead {lead_id} scored: {ai_result.get('score')} "
        f"by {created_by} — '{body.note[:50]}...'"
    )

    return {
        "note_id":         note_row.id,
        "lead_id":         lead_id,
        "note":            body.note,
        "created_by":      created_by,
        "created_at":      note_row.created_at.isoformat(),
        "ai_score":        ai_result.get("score"),
        "ai_score_label":  SCORE_DISPLAY.get(ai_result.get("score", ""), ""),
        "ai_score_reason": ai_result.get("reason"),
        "follow_up_days":  follow_up_days,
        "follow_up_date":  follow_up_date.isoformat() if follow_up_date else None,
        "signals":         ai_result.get("signals", []),
    }


@router.get("/leads/{lead_id}/notes")
def get_notes(
    lead_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get full notes history for a lead, newest first."""
    rows = db.execute(text("""
        SELECT id, lead_id, note, created_by,
               ai_score, ai_score_reason,
               follow_up_days, signals, created_at
        FROM lead_notes
        WHERE lead_id = :lead_id
        ORDER BY created_at DESC
    """), {"lead_id": lead_id}).fetchall()

    return [
        {
            "id":              r.id,
            "lead_id":         r.lead_id,
            "note":            r.note,
            "created_by":      r.created_by,
            "ai_score":        r.ai_score,
            "ai_score_label":  SCORE_DISPLAY.get(r.ai_score or "", ""),
            "ai_score_reason": r.ai_score_reason,
            "follow_up_days":  r.follow_up_days,
            "signals":         r.signals or [],
            "created_at":      r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/leads/follow-ups")
def get_follow_ups(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Leads due for follow-up today or overdue.
    Powers a follow-up reminder section in the dashboard.
    """
    rows = db.execute(text("""
        SELECT
            l.id, l.name, l.phone, l.area,
            l.lead_type, l.ai_score, l.follow_up_date,
            l.last_contacted, l.assigned_to
        FROM leads l
        WHERE l.follow_up_date <= CURRENT_DATE
          AND l.status NOT IN ('won', 'lost')
          AND l.follow_up_date IS NOT NULL
        ORDER BY l.follow_up_date ASC
        LIMIT 20
    """)).fetchall()

    return [
        {
            "id":             r.id,
            "name":           r.name,
            "phone":          r.phone,
            "area":           r.area,
            "lead_type":      r.lead_type,
            "ai_score":       r.ai_score,
            "ai_score_label": SCORE_DISPLAY.get(r.ai_score or "", ""),
            "follow_up_date": r.follow_up_date.isoformat() if r.follow_up_date else None,
            "last_contacted": r.last_contacted.isoformat() if r.last_contacted else None,
            "days_overdue":   (date.today() - r.follow_up_date).days if r.follow_up_date else 0,
        }
        for r in rows
    ]


@router.get("/leads/{lead_id}/detail")
def get_lead_detail(
    lead_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Full lead detail including latest note — powers the lead detail panel."""
    row = db.execute(text("""
        SELECT
            l.id, l.name, l.owner_name,
            l.phone, l.email, l.website,
            l.area, l.lead_type, l.source,
            l.score, l.ai_score, l.ai_score_reason,
            l.ai_scored_at, l.status, l.notes,
            l.assigned_to, l.last_contacted,
            l.contact_attempts, l.follow_up_date,
            l.created_at, l.updated_at
        FROM leads l
        WHERE l.id = :id
    """), {"id": lead_id}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Get latest note
    latest_note = db.execute(text("""
        SELECT note, created_by, ai_score, ai_score_reason, created_at
        FROM lead_notes
        WHERE lead_id = :id
        ORDER BY created_at DESC
        LIMIT 1
    """), {"id": lead_id}).fetchone()

    return {
        "id":               row.id,
        "name":             row.name,
        "owner_name":       row.owner_name,
        "phone":            row.phone,
        "email":            row.email,
        "website":          row.website,
        "area":             row.area,
        "lead_type":        row.lead_type,
        "source":           row.source,
        "score":            row.score,
        "ai_score":         row.ai_score,
        "ai_score_label":   SCORE_DISPLAY.get(row.ai_score or "", ""),
        "ai_score_reason":  row.ai_score_reason,
        "ai_scored_at":     row.ai_scored_at.isoformat() if row.ai_scored_at else None,
        "status":           row.status,
        "assigned_to":      row.assigned_to,
        "last_contacted":   row.last_contacted.isoformat() if row.last_contacted else None,
        "contact_attempts": row.contact_attempts or 0,
        "follow_up_date":   row.follow_up_date.isoformat() if row.follow_up_date else None,
        "created_at":       row.created_at.isoformat() if row.created_at else None,
        "updated_at":       row.updated_at.isoformat() if row.updated_at else None,
        "latest_note": {
            "note":            latest_note.note,
            "created_by":      latest_note.created_by,
            "ai_score":        latest_note.ai_score,
            "ai_score_label":  SCORE_DISPLAY.get(latest_note.ai_score or "", ""),
            "ai_score_reason": latest_note.ai_score_reason,
            "created_at":      latest_note.created_at.isoformat(),
        } if latest_note else None,
    }