"""
reports.py — Weekly pipeline report
Powers a Reports tab in the dashboard showing sales activity
plus scraping coverage by area and lead type.

Add to main.py:
    from app.routers import reports
    app.include_router(reports.router)
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/weekly")
def get_weekly_report(
    days: int = Query(7, ge=1, le=31),
    db: Session = Depends(get_db),
):
    """
    Weekly pipeline report — sales activity + scraping coverage.

    Sales activity covers the last `days` days (default 7).
    Coverage is a snapshot of the entire leads table right now —
    it shows what areas/types have data, not just this week's additions.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # ── SALES ACTIVITY ────────────────────────────────────────────

    activity = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE created_at >= :since)                AS new_leads,
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

    # ── TOP PERFORMER THIS WEEK ──────────────────────────────────

    top_performer = db.execute(text("""
        SELECT
            assigned_to,
            COUNT(*) FILTER (WHERE status = 'called')      AS calls,
            COUNT(*) FILTER (WHERE status = 'demo_booked') AS demos,
            COUNT(*) FILTER (WHERE status = 'won')          AS won
        FROM leads
        WHERE assigned_to IS NOT NULL
          AND updated_at >= :since
        GROUP BY assigned_to
        ORDER BY calls DESC, demos DESC
        LIMIT 1
    """), {"since": since}).fetchone()

    # ── LEADS NEEDING FOLLOW-UP ───────────────────────────────────

    follow_ups = db.execute(text("""
        SELECT id, name, area, lead_type,
               follow_up_date,
               (CURRENT_DATE - follow_up_date) AS days_overdue
        FROM leads
        WHERE follow_up_date IS NOT NULL
          AND follow_up_date <= CURRENT_DATE
          AND status NOT IN ('won', 'lost')
        ORDER BY follow_up_date ASC
        LIMIT 10
    """)).fetchall()

    # ── COVERAGE BY AREA + LEAD TYPE ──────────────────────────────
    # Snapshot of the entire leads table — shows where data exists
    # and implicitly where it doesn't (gaps to scrape next).

    coverage_rows = db.execute(text("""
        SELECT
            area,
            lead_type,
            COUNT(*) AS count
        FROM leads
        WHERE area IS NOT NULL
          AND lead_type != 'developer'
        GROUP BY area, lead_type
        ORDER BY area, lead_type
    """)).fetchall()

    # Developers tracked separately — they're not tied to one area
    developer_count = db.execute(text("""
        SELECT COUNT(*) FROM leads WHERE lead_type = 'developer'
    """)).scalar()

    # Reshape into: { area: { apartment: N, agency: N, landlord: N } }
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

    # ── KNOWN NAIROBI AREAS NOT YET SCRAPED AT ALL ────────────────
    # Areas your scrapers target but have zero records — true gaps.

    KNOWN_AREAS = [
        "Kilimani", "Kileleshwa", "Westlands", "Lavington",
        "South B", "South C", "Parklands", "Muthaiga",
        "Karen", "Runda", "Spring Valley", "Ruaka",
        "Syokimau", "Kasarani", "Ngara", "Pangani",
        "Embakasi", "Langata", "Kahawa West", "Donholm",
    ]
    covered_areas = set(coverage.keys())
    untapped = [a for a in KNOWN_AREAS if a not in covered_areas]

    return {
        "period_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),

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
                "id":            r.id,
                "name":          r.name,
                "area":          r.area,
                "lead_type":     r.lead_type,
                "follow_up_date": r.follow_up_date.isoformat() if r.follow_up_date else None,
                "days_overdue":  r.days_overdue,
            }
            for r in follow_ups
        ],

        "coverage": coverage_list,
        "untapped_areas": untapped,
        "developers_tracked": developer_count,
    }