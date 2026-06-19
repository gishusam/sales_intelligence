# backend/app/routers/leads.py
# All routes for the dashboard and lead management.
#
# Add to main.py:
#   from app.routers import leads
#   app.include_router(leads.router)

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/api", tags=["leads"])


# ── Pydantic schemas ───────────────────────────────────────────────

class StatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None
    assigned_to: Optional[str] = None


class LeadUpdate(BaseModel):
    notes: Optional[str] = None
    assigned_to: Optional[str] = None


# ── 1. Dashboard summary — powers the 6 KPI cards ─────────────────

@router.get("/dashboard/summary")
def get_summary(db: Session = Depends(get_db)):
    """
    Returns all 6 KPI numbers in one query.
    Frontend hits this once on page load.
    """
    row = db.execute(text("""
        SELECT
            COUNT(*)                                            AS total_leads,
            COUNT(*) FILTER (WHERE status = 'new')             AS new_leads,
            COUNT(*) FILTER (
                WHERE status = 'called'
                AND updated_at >= NOW() - INTERVAL '7 days'
            )                                                   AS calls_this_week,
            COUNT(*) FILTER (WHERE status = 'demo_booked')     AS demos_booked,
            COUNT(*) FILTER (WHERE status = 'won')             AS won_customers,
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE status = 'won')
                / NULLIF(COUNT(*), 0),
                1
            )                                                   AS conversion_rate
        FROM leads
    """)).fetchone()

    return {
        "total_leads":      row.total_leads,
        "new_leads":        row.new_leads,
        "calls_this_week":  row.calls_this_week,
        "demos_booked":     row.demos_booked,
        "won_customers":    row.won_customers,
        "conversion_rate":  float(row.conversion_rate or 0),
    }


# ── 2. Leads by source — powers the donut chart ───────────────────

@router.get("/dashboard/by-source")
def get_by_source(db: Session = Depends(get_db)):
    """
    Returns count per lead_type for the donut chart.
    apartment → Apartments (green)
    agency    → Agencies (blue)
    landlord  → Landlords (orange)
    """
    rows = db.execute(text("""
        SELECT lead_type, COUNT(*) AS count
        FROM leads
        GROUP BY lead_type
        ORDER BY count DESC
    """)).fetchall()

    return [{"type": r.lead_type, "count": r.count} for r in rows]


# ── 3. Lead status funnel — powers horizontal bars ────────────────

@router.get("/dashboard/funnel")
def get_funnel(db: Session = Depends(get_db)):
    """
    Returns count per status in pipeline order.
    """
    rows = db.execute(text("""
        SELECT status, COUNT(*) AS count
        FROM leads
        GROUP BY status
        ORDER BY
            CASE status
                WHEN 'new'         THEN 1
                WHEN 'called'      THEN 2
                WHEN 'demo_booked' THEN 3
                WHEN 'won'         THEN 4
                WHEN 'lost'        THEN 5
                ELSE 6
            END
    """)).fetchall()

    return [{"status": r.status, "count": r.count} for r in rows]


# ── 4. Leads by area — powers the bar chart ───────────────────────

@router.get("/dashboard/by-area")
def get_by_area(db: Session = Depends(get_db)):
    """
    Returns top 12 areas by lead count for the bar chart.
    """
    rows = db.execute(text("""
        SELECT area, COUNT(*) AS count
        FROM leads
        WHERE area IS NOT NULL
        GROUP BY area
        ORDER BY count DESC
        LIMIT 12
    """)).fetchall()

    return [{"area": r.area, "count": r.count} for r in rows]


# ── 5. Recent activity — powers the activity feed ─────────────────

@router.get("/dashboard/activity")
def get_activity(db: Session = Depends(get_db)):
    """
    Returns last 10 leads that were updated — shows sales team activity.
    """
    rows = db.execute(text("""
        SELECT
            id, name, lead_type, area,
            status, assigned_to, notes,
            updated_at
        FROM leads
        WHERE updated_at IS NOT NULL
          AND status != 'new'
        ORDER BY updated_at DESC
        LIMIT 10
    """)).fetchall()

    return [
        {
            "id":           r.id,
            "name":         r.name,
            "lead_type":    r.lead_type,
            "area":         r.area,
            "status":       r.status,
            "assigned_to":  r.assigned_to,
            "notes":        r.notes,
            "updated_at":   r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]


# ── 6. Lead list — powers all four sidebar tabs ───────────────────

@router.get("/leads")
def get_leads(
    lead_type:   Optional[str] = Query(None),  # apartment/agency/landlord
    status:      Optional[str] = Query(None),  # new/called/demo_booked/won/lost
    area:        Optional[str] = Query(None),
    assigned_to: Optional[str] = Query(None),
    page:        int = Query(1, ge=1),
    limit:       int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Paginated lead list with filters.
    Powers: Apartments tab, Agencies tab, Landlords tab, My Leads tab.

    Examples:
        /api/leads?lead_type=apartment
        /api/leads?lead_type=agency&status=new
        /api/leads?assigned_to=Brian&status=called
        /api/leads?area=Kilimani
    """
    filters = ["1=1"]
    params  = {}

    if lead_type:
        filters.append("lead_type = :lead_type")
        params["lead_type"] = lead_type
    if status:
        filters.append("status = :status")
        params["status"] = status
    if area:
        filters.append("area ILIKE :area")
        params["area"] = f"%{area}%"
    if assigned_to:
        filters.append("assigned_to = :assigned_to")
        params["assigned_to"] = assigned_to

    where = " AND ".join(filters)
    offset = (page - 1) * limit

    # Total count for pagination
    total = db.execute(
        text(f"SELECT COUNT(*) FROM leads WHERE {where}"), params
    ).scalar()

    # Actual rows
    rows = db.execute(text(f"""
        SELECT
            id, name, owner_name, phone, email, website,
            area, lead_type, source,
            score, status, notes, assigned_to,
            last_contacted, created_at, updated_at
        FROM leads
        WHERE {where}
        ORDER BY score DESC, created_at DESC
        LIMIT :limit OFFSET :offset
    """), {**params, "limit": limit, "offset": offset}).fetchall()

    return {
        "total": total,
        "page":  page,
        "limit": limit,
        "pages": -(-total // limit),   # ceiling division
        "data": [
            {
                "id":             r.id,
                "name":           r.name,
                "owner_name":     r.owner_name,
                "phone":          r.phone,
                "email":          r.email,
                "website":        r.website,
                "area":           r.area,
                "lead_type":      r.lead_type,
                "source":         r.source,
                "score":          r.score,
                "status":         r.status,
                "notes":          r.notes,
                "assigned_to":    r.assigned_to,
                "last_contacted": r.last_contacted.isoformat() if r.last_contacted else None,
                "created_at":     r.created_at.isoformat() if r.created_at else None,
                "updated_at":     r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    }


# ── 7. Search — powers the search bar ────────────────────────────

@router.get("/leads/search")
def search_leads(
    q:     str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """
    Full-text search across name, owner_name, area.
    Powers the search bar at the top of the dashboard.
    """
    rows = db.execute(text("""
        SELECT
            id, name, owner_name, phone,
            area, lead_type, status, score
        FROM leads
        WHERE
            name       ILIKE :q OR
            owner_name ILIKE :q OR
            area       ILIKE :q
        ORDER BY score DESC
        LIMIT :limit
    """), {"q": f"%{q}%", "limit": limit}).fetchall()

    return [
        {
            "id":        r.id,
            "name":      r.name,
            "owner_name":r.owner_name,
            "phone":     r.phone,
            "area":      r.area,
            "lead_type": r.lead_type,
            "status":    r.status,
            "score":     r.score,
        }
        for r in rows
    ]


# ── 8. Update status — rep marks a lead as called/won/lost ────────

VALID_STATUSES = {"new", "called", "demo_booked", "won", "lost"}

@router.patch("/leads/{lead_id}/status")
def update_status(
    lead_id: int,
    body:    StatusUpdate,
    db:      Session = Depends(get_db),
):
    """
    Sales rep updates lead status after a call.
    Also saves optional notes and assigned_to.
    """
    if body.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {VALID_STATUSES}"
        )

    result = db.execute(text("""
        UPDATE leads SET
            status         = :status,
            notes          = COALESCE(:notes, notes),
            assigned_to    = COALESCE(:assigned_to, assigned_to),
            last_contacted = CASE
                WHEN :status IN ('called', 'demo_booked', 'won', 'lost')
                THEN NOW() ELSE last_contacted
            END,
            updated_at     = NOW()
        WHERE id = :id
        RETURNING id, name, status
    """), {
        "id":          lead_id,
        "status":      body.status,
        "notes":       body.notes,
        "assigned_to": body.assigned_to,
    })
    db.commit()

    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")

    return {"id": row.id, "name": row.name, "status": row.status}


# ── 9. Update notes — rep saves call notes ────────────────────────

@router.patch("/leads/{lead_id}/notes")
def update_notes(
    lead_id: int,
    body:    LeadUpdate,
    db:      Session = Depends(get_db),
):
    """Update notes and/or assigned_to without changing status."""
    result = db.execute(text("""
        UPDATE leads SET
            notes       = COALESCE(:notes, notes),
            assigned_to = COALESCE(:assigned_to, assigned_to),
            updated_at  = NOW()
        WHERE id = :id
        RETURNING id, name
    """), {
        "id":          lead_id,
        "notes":       body.notes,
        "assigned_to": body.assigned_to,
    })
    db.commit()

    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")

    return {"id": row.id, "name": row.name, "updated": True}