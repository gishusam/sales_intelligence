"""
reports.py — Weekly pipeline report with AI narrative + download
Powers the Reports tab in the dashboard.

Add to main.py:
    from app.routers import reports
    app.include_router(reports.router)
"""

import os
import json
import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query
from app.auth import get_current_user, CurrentUser
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database import get_db

router = APIRouter(prefix="/api/reports", tags=["reports"])

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"

# ── Narrative generator ────────────────────────────────────────────
async def generate_narrative(report_data: dict) -> str:
    """Use Groq/Llama to write a human narrative from the weekly numbers."""
    if not GROQ_API_KEY:
        return "AI narrative unavailable — GROQ_API_KEY not configured."

    activity   = report_data.get("activity", {})
    coverage   = report_data.get("coverage", [])
    follow_ups = report_data.get("follow_ups_due", [])
    top        = report_data.get("top_performer")
    untapped   = report_data.get("untapped_areas", [])

    prompt = f"""You are a sales intelligence analyst writing a weekly report
for Nyumba Zetu, a property management software company in Nairobi, Kenya.
Write a clear, professional 3-paragraph narrative summary of this week's
sales pipeline performance. Be specific, use the actual numbers, and end
with one actionable recommendation for next week.

DATA:
- New leads added: {activity.get('new_leads', 0)}
- Calls made: {activity.get('calls_made', 0)}
- Demos booked: {activity.get('demos_booked', 0)}
- Deals won: {activity.get('won', 0)}
- Deals lost: {activity.get('lost', 0)}
- Top performer: {top['name'] + ' (' + str(top['calls']) + ' calls, ' + str(top['won']) + ' won)' if top else 'No activity recorded'}
- Follow-ups due or overdue: {len(follow_ups)}
- Areas with scraped data: {len(coverage)}
- Untapped areas remaining: {len(untapped)} ({', '.join(untapped[:5])}{'...' if len(untapped) > 5 else ''})

Write in a professional tone. Keep it under 200 words. No bullet points.
End with "Next week:" followed by one specific action the team should take."""

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       "llama-3.1-8b-instant",
                    "messages":    [{"role": "user", "content": prompt}],
                    "max_tokens":  400,
                    "temperature": 0.4,
                }
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Narrative generation failed: {str(e)[:100]}"

# ── Weekly report ──────────────────────────────────────────────────
@router.get("/weekly")
async def get_weekly_report(
    days:      int  = Query(7, ge=1, le=31),
    narrative: bool = Query(False),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Weekly pipeline report — sales activity + scraping coverage.
    Add ?narrative=true to include AI-generated narrative summary.
    Add ?days=30 for monthly view.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # ── Sales activity ─────────────────────────────────────────────
    activity = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE created_at >= :since)
                AS new_leads,
            COUNT(*) FILTER (
                WHERE status = 'called' AND updated_at >= :since
            )                                                            AS calls_made,
            COUNT(*) FILTER (
                WHERE status = 'demo_booked' AND updated_at >= :since
            )                                                            AS demos_booked,
            COUNT(*) FILTER (
                WHERE status = 'won' AND updated_at >= :since
            )                                                            AS won,
            COUNT(*) FILTER (
                WHERE status = 'lost' AND updated_at >= :since
            )                                                            AS lost
        FROM leads
    """), {"since": since}).fetchone()

    # ── Top performer ──────────────────────────────────────────────
    top_performer = db.execute(text("""
        SELECT
            assigned_to,
            COUNT(*) FILTER (WHERE status = 'called')      AS calls,
            COUNT(*) FILTER (WHERE status = 'demo_booked') AS demos,
            COUNT(*) FILTER (WHERE status = 'won')         AS won
        FROM leads
        WHERE assigned_to IS NOT NULL
          AND updated_at >= :since
        GROUP BY assigned_to
        ORDER BY calls DESC, demos DESC
        LIMIT 1
    """), {"since": since}).fetchone()

    # ── Follow-ups due (next 7 days + overdue) ────────────────────
    follow_ups = db.execute(text("""
        SELECT
            id, name, area, lead_type,
            follow_up_date,
            (CURRENT_DATE - follow_up_date) AS days_overdue,
            CASE
                WHEN follow_up_date < CURRENT_DATE  THEN 'overdue'
                WHEN follow_up_date = CURRENT_DATE  THEN 'due_today'
                ELSE 'upcoming'
            END AS urgency
        FROM leads
        WHERE follow_up_date IS NOT NULL
          AND follow_up_date <= CURRENT_DATE + INTERVAL '7 days'
          AND status NOT IN ('won', 'lost')
        ORDER BY follow_up_date ASC
        LIMIT 20
    """)).fetchall()

    # ── Coverage by area + lead type ───────────────────────────────
    coverage_rows = db.execute(text("""
        SELECT area, lead_type, COUNT(*) AS count
        FROM leads
        WHERE area IS NOT NULL
          AND lead_type != 'developer'
        GROUP BY area, lead_type
        ORDER BY area, lead_type
    """)).fetchall()

    coverage = {}
    for r in coverage_rows:
        if r.area not in coverage:
            coverage[r.area] = {"apartment": 0, "agency": 0, "landlord": 0}
        coverage[r.area][r.lead_type] = r.count

    coverage_list = [
        {
            "area":      area,
            "apartment": counts.get("apartment", 0),
            "agency":    counts.get("agency", 0),
            "landlord":  counts.get("landlord", 0),
            "total":     sum(counts.values()),
        }
        for area, counts in coverage.items()
    ]
    coverage_list.sort(key=lambda x: x["total"], reverse=True)

    # ── Developer count (not area-bound) ──────────────────────────
    developer_count = db.execute(
        text("SELECT COUNT(*) FROM leads WHERE lead_type = 'developer'")
    ).scalar()

    # ── Untapped areas ─────────────────────────────────────────────
    KNOWN_AREAS = [
        "Kilimani", "Kileleshwa", "Westlands", "Lavington",
        "South B", "South C", "Parklands", "Muthaiga",
        "Karen", "Runda", "Spring Valley", "Ruaka",
        "Syokimau", "Kasarani", "Ngara", "Pangani",
        "Embakasi", "Langata", "Kahawa West", "Donholm",
    ]
    covered_areas = set(coverage.keys())
    untapped = [a for a in KNOWN_AREAS if a not in covered_areas]

    report = {
        "period_days":       days,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "activity": {
            "new_leads":    activity.new_leads,
            "calls_made":   activity.calls_made,
            "demos_booked": activity.demos_booked,
            "won":          activity.won,
            "lost":         activity.lost,
        },
        "top_performer": {
            "name":  top_performer.assigned_to,
            "calls": top_performer.calls,
            "demos": top_performer.demos,
            "won":   top_performer.won,
        } if top_performer and top_performer.assigned_to else None,
        "follow_ups_due": [
            {
                "id":             r.id,
                "name":           r.name,
                "area":           r.area,
                "lead_type":      r.lead_type,
                "follow_up_date": r.follow_up_date.isoformat() if r.follow_up_date else None,
                "days_overdue":   r.days_overdue,
                "urgency":        r.urgency,
            }
            for r in follow_ups
        ],
        "coverage":          coverage_list,
        "untapped_areas":    untapped,
        "developers_tracked": developer_count,
        "narrative":         None,
    }

    # Generate AI narrative only if requested — adds ~2 seconds
    if narrative:
        report["narrative"] = await generate_narrative(report)

    return report

# ── Narrative-only endpoint ────────────────────────────────────────
@router.get("/weekly/narrative")
async def get_narrative_only(
    days: int = Query(7, ge=1, le=31),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Returns just the AI narrative. Frontend calls this separately
    so the main report loads fast and narrative loads after.
    """
    # Get the full report data to feed to the AI
    full = await get_weekly_report(days=days, narrative=False, db=db)
    narrative = await generate_narrative(full)
    return {
        "narrative":    narrative,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_days":  days,
    }
