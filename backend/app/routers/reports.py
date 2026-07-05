
"""
reports.py — Weekly pipeline report + AI narrative
GET /api/reports/weekly              — full report, no AI
GET /api/reports/weekly?narrative=true — full report + AI narrative
GET /api/reports/weekly/narrative    — AI narrative only
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, CurrentUser

router = APIRouter(prefix="/api/reports", tags=["reports"])
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama3-8b-8192")

KNOWN_AREAS = [
    "Kilimani", "Kileleshwa", "Westlands", "Lavington",
    "Parklands", "Lenana Road", "Kindaruma Road", "Mugunga Road",
    "South C", "South B", "Karen", "Runda", "Spring Valley",
    "Ruaka", "Muthaiga", "Syokimau", "Kasarani", "Ngara",
    "Pangani", "Embakasi", "Langata", "Kahawa West", "Donholm",
]


# ── AI narrative ───────────────────────────────────────────────────

def build_prompt(report_data: dict) -> str:
    activity  = report_data.get("activity", {})
    pipeline  = report_data.get("pipeline", {})
    follow_up = report_data.get("follow_ups", {})
    coverage  = report_data.get("scraped_this_week", [])
    untapped  = report_data.get("untapped_this_week", [])
    days      = report_data.get("period_days", 7)

    top_areas = sorted(
        coverage, key=lambda x: x.get("imported", 0), reverse=True
    )[:3]
    top_area_names = ", ".join(a["area"] for a in top_areas) if top_areas else "none"

    return f"""You are a sales intelligence analyst for Nyumba Zetu, 
a property management software company in Nairobi.

Write a concise {days}-day pipeline report in 3 short paragraphs — 
professional, direct, no fluff. Use the data below.

DATA:
- New leads discovered: {activity.get('new_leads', 0)}
- Calls made: {activity.get('calls_made', 0)}
- Demos booked: {activity.get('demos_booked', 0)}
- Won this period: {activity.get('won', 0)}
- Total leads in system: {pipeline.get('total', 0)}
- Apartments: {pipeline.get('apartments', 0)}
- Agencies: {pipeline.get('agencies', 0)}
- Developers: {pipeline.get('developers', 0)}
- Overdue follow-ups: {follow_up.get('overdue', 0)}
- Due today: {follow_up.get('due_today', 0)}
- Top scraped areas this period: {top_area_names}
- Untapped areas: {', '.join(untapped[:5]) if untapped else 'none'}

Paragraph 1: What happened this period (activity summary)
Paragraph 2: Pipeline health and where the opportunities are
Paragraph 3: What the team should focus on next — be specific about areas and lead types

Return plain text only, no markdown, no bullet points."""


async def generate_narrative(report_data: dict) -> str:
    if not GROQ_API_KEY:
        return "Narrative unavailable — GROQ_API_KEY not configured."

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":    GROQ_MODEL,
                    "messages": [
                        {"role": "user", "content": build_prompt(report_data)}
                    ],
                    "max_tokens":   400,
                    "temperature":  0.4,
                }
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

    except httpx.TimeoutException:
        logger.warning("Groq narrative timed out")
        return "Narrative timed out — try again in a moment."
    except httpx.HTTPStatusError as e:
        logger.error(f"Groq HTTP error: {e.response.status_code}")
        return f"AI service error ({e.response.status_code}) — try again."
    except Exception as e:
        logger.error(f"Narrative generation failed: {e}")
        return f"Narrative unavailable: {str(e)[:100]}"


# ── Data fetching ──────────────────────────────────────────────────

def _build_report_data(db: Session, days: int) -> dict:
    """
    Single function that fetches all report data.
    Both endpoints call this — no double DB hits.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # ── Activity this period ──────────────────────────────────────
    activity_row = db.execute(text("""
        SELECT
            COUNT(*) FILTER (
                WHERE created_at >= :since
            ) AS new_leads,
            COUNT(*) FILTER (
                WHERE status IN ('called','Called')
                AND updated_at >= :since
            ) AS calls_made,
            COUNT(*) FILTER (
                WHERE status IN ('demo_booked','Demo Booked')
                AND updated_at >= :since
            ) AS demos_booked,
            COUNT(*) FILTER (
                WHERE status IN ('won','Won')
                AND updated_at >= :since
            ) AS won
        FROM leads
    """), {"since": since}).fetchone()

    # ── Pipeline totals ───────────────────────────────────────────
    pipeline_row = db.execute(text("""
        SELECT
            COUNT(*)                                              AS total,
            COUNT(*) FILTER (WHERE lead_type = 'apartment')      AS apartments,
            COUNT(*) FILTER (WHERE lead_type = 'agency')         AS agencies,
            COUNT(*) FILTER (WHERE lead_type = 'landlord')       AS landlords,
            COUNT(*) FILTER (WHERE lead_type = 'developer')      AS developers,
            COUNT(*) FILTER (WHERE status NOT IN ('new','New'))  AS contacted,
            COUNT(*) FILTER (WHERE phone IS NOT NULL)            AS with_phone,
            COUNT(*) FILTER (WHERE email IS NOT NULL)            AS with_email,
            COUNT(*) FILTER (WHERE ai_score IS NOT NULL)         AS ai_scored
        FROM leads
    """)).fetchone()

    # ── Follow-ups ────────────────────────────────────────────────
    followup_row = db.execute(text("""
        SELECT
            COUNT(*) FILTER (
                WHERE follow_up_date < CURRENT_DATE
            ) AS overdue,
            COUNT(*) FILTER (
                WHERE follow_up_date = CURRENT_DATE
            ) AS due_today,
            COUNT(*) FILTER (
                WHERE follow_up_date BETWEEN CURRENT_DATE + 1
                AND CURRENT_DATE + 7
            ) AS upcoming
        FROM leads
        WHERE follow_up_date IS NOT NULL
          AND status NOT IN ('won','Won','lost','Lost')
    """)).fetchone()

    # ── Follow-up detail list ─────────────────────────────────────
    followup_leads = db.execute(text("""
        SELECT
            id, name, phone, area,
            lead_type, ai_score, follow_up_date,
            last_contacted,
            GREATEST(0, CURRENT_DATE - follow_up_date) AS days_overdue,
            CASE
                WHEN follow_up_date < CURRENT_DATE  THEN 'overdue'
                WHEN follow_up_date = CURRENT_DATE  THEN 'due_today'
                ELSE 'upcoming'
            END AS urgency
        FROM leads
        WHERE follow_up_date IS NOT NULL
          AND follow_up_date <= CURRENT_DATE + 7
          AND status NOT IN ('won','Won','lost','Lost')
        ORDER BY follow_up_date ASC
        LIMIT 20
    """)).fetchall()

    # ── This week's scrape runs ───────────────────────────────────
    scrape_rows = db.execute(text("""
        SELECT
            unnested_area AS area,
            scraper_type,
            SUM(records_found) AS records_found,
            SUM(imported)      AS imported,
            SUM(duplicates)    AS duplicates,
            SUM(rejected)      AS rejected,
            COUNT(*)           AS run_count,
            MAX(finished_at)   AS last_run
        FROM scraper_runs,
             UNNEST(areas) AS unnested_area
        WHERE started_at >= :since
          AND status = 'success'
        GROUP BY unnested_area, scraper_type
        ORDER BY unnested_area, scraper_type
    """), {"since": since}).fetchall()

    scraped_areas = {r.area.lower() for r in scrape_rows}

    # ── AI score distribution ─────────────────────────────────────
    score_rows = db.execute(text("""
        SELECT ai_score, COUNT(*) AS count
        FROM leads
        WHERE ai_score IS NOT NULL
        GROUP BY ai_score
        ORDER BY count DESC
    """)).fetchall()

    return {
        "period_days":  days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "activity": {
            "new_leads":   activity_row.new_leads   or 0,
            "calls_made":  activity_row.calls_made  or 0,
            "demos_booked":activity_row.demos_booked or 0,
            "won":         activity_row.won          or 0,
        },
        "pipeline": {
            "total":      pipeline_row.total      or 0,
            "apartments": pipeline_row.apartments or 0,
            "agencies":   pipeline_row.agencies   or 0,
            "landlords":  pipeline_row.landlords  or 0,
            "developers": pipeline_row.developers or 0,
            "contacted":  pipeline_row.contacted  or 0,
            "with_phone": pipeline_row.with_phone or 0,
            "with_email": pipeline_row.with_email or 0,
            "ai_scored":  pipeline_row.ai_scored  or 0,
        },
        "follow_ups": {
            "overdue":   followup_row.overdue   or 0,
            "due_today": followup_row.due_today or 0,
            "upcoming":  followup_row.upcoming  or 0,
            "leads": [
                {
                    "id":             r.id,
                    "name":           r.name,
                    "phone":          r.phone,
                    "area":           r.area,
                    "lead_type":      r.lead_type,
                    "ai_score":       r.ai_score,
                    "follow_up_date": r.follow_up_date.isoformat() if r.follow_up_date else None,
                    "last_contacted": r.last_contacted.isoformat() if r.last_contacted else None,
                    "days_overdue":   r.days_overdue,
                    "urgency":        r.urgency,
                }
                for r in followup_leads
            ],
        },
        "scraped_this_week": [
            {
                "area":          r.area,
                "scraper_type":  r.scraper_type,
                "records_found": r.records_found or 0,
                "imported":      r.imported      or 0,
                "duplicates":    r.duplicates    or 0,
                "rejected":      r.rejected      or 0,
                "run_count":     r.run_count,
                "last_run":      r.last_run.isoformat() if r.last_run else None,
            }
            for r in scrape_rows
        ],
        "untapped_this_week": [
            a for a in KNOWN_AREAS
            if a.lower() not in scraped_areas
        ],
        "ai_scores": {
            r.ai_score: r.count for r in score_rows
        },
    }


# ── Endpoints ──────────────────────────────────────────────────────

@router.get("/weekly")
async def get_weekly_report(
    days:      int  = Query(7, ge=1, le=31),
    narrative: bool = Query(False),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    report = _build_report_data(db, days)

    if narrative:
        report["narrative"] = await generate_narrative(report)

    return report


@router.get("/weekly/narrative")
async def get_narrative_only(
    days: int = Query(7, ge=1, le=31),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Narrative only — frontend calls this separately so main report loads fast."""
    report = _build_report_data(db, days)
    narrative = await generate_narrative(report)
    return {
        "narrative":    narrative,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_days":  days,
    }
