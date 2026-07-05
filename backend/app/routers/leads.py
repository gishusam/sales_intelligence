# backend/app/routers/leads.py
# Full leads router with:
#   - All original 9 endpoints
#   - Feature 1: Lead detail + contact timeline (lead_events)
#   - Feature 2: Lead type summary strip
#   - Feature 3: Bulk CSV upload with import report

import io
import csv
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/api", tags=["leads"])


# ── Pydantic schemas ───────────────────────────────────────────────

class StatusUpdate(BaseModel):
    status:      str
    notes:       Optional[str] = None
    assigned_to: Optional[str] = None
    changed_by:  Optional[str] = None


class LeadUpdate(BaseModel):
    notes:       Optional[str] = None
    assigned_to: Optional[str] = None


# ── 1. Dashboard summary ───────────────────────────────────────────

@router.get("/dashboard/summary")
def get_summary(db: Session = Depends(get_db)):
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
                / NULLIF(COUNT(*), 0), 1
            )                                                   AS conversion_rate
        FROM leads
    """)).fetchone()

    return {
        "total_leads":     row.total_leads,
        "new_leads":       row.new_leads,
        "calls_this_week": row.calls_this_week,
        "demos_booked":    row.demos_booked,
        "won_customers":   row.won_customers,
        "conversion_rate": float(row.conversion_rate or 0),
    }


# ── 2. Leads by source ─────────────────────────────────────────────

@router.get("/dashboard/by-source")
def get_by_source(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT lead_type, COUNT(*) AS count
        FROM leads GROUP BY lead_type ORDER BY count DESC
    """)).fetchall()
    return [{"type": r.lead_type, "count": r.count} for r in rows]


# ── 3. Funnel ──────────────────────────────────────────────────────

@router.get("/dashboard/funnel")
def get_funnel(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT status, COUNT(*) AS count FROM leads
        GROUP BY status
        ORDER BY CASE status
            WHEN 'new' THEN 1 WHEN 'called' THEN 2
            WHEN 'demo_booked' THEN 3 WHEN 'won' THEN 4
            WHEN 'lost' THEN 5 ELSE 6 END
    """)).fetchall()
    return [{"status": r.status, "count": r.count} for r in rows]


# ── 4. By area ─────────────────────────────────────────────────────

@router.get("/dashboard/by-area")
def get_by_area(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT area, COUNT(*) AS count FROM leads
        WHERE area IS NOT NULL
        GROUP BY area ORDER BY count DESC LIMIT 12
    """)).fetchall()
    return [{"area": r.area, "count": r.count} for r in rows]


# ── 5. Recent activity ─────────────────────────────────────────────

@router.get("/dashboard/activity")
def get_activity(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, name, lead_type, area,
               status, assigned_to, notes, updated_at
        FROM leads
        WHERE updated_at IS NOT NULL AND status != 'new'
        ORDER BY updated_at DESC LIMIT 10
    """)).fetchall()
    return [
        {
            "id": r.id, "name": r.name, "lead_type": r.lead_type,
            "area": r.area, "status": r.status,
            "assigned_to": r.assigned_to, "notes": r.notes,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]


# ── FEATURE 2: Lead type summary strip ────────────────────────────
# Powers the strip at the top of the leads page showing:
# Apartments: 211 total | 45 contacted | 12 won

@router.get("/leads/summary")
def get_leads_summary(db: Session = Depends(get_db)):
    """
    Returns pipeline health per lead type.
    'contacted' = any status that is not 'new'.
    """
    rows = db.execute(text("""
        SELECT
            lead_type,
            COUNT(*)                                        AS total,
            COUNT(*) FILTER (WHERE status != 'new')        AS contacted,
            COUNT(*) FILTER (WHERE status = 'won')         AS won,
            COUNT(*) FILTER (WHERE status = 'demo_booked') AS demo_booked,
            COUNT(*) FILTER (WHERE status = 'called')      AS called,
            COUNT(*) FILTER (WHERE status = 'lost')        AS lost
        FROM leads
        GROUP BY lead_type
        ORDER BY total DESC
    """)).fetchall()

    return [
        {
            "lead_type":   r.lead_type,
            "total":       r.total,
            "contacted":   r.contacted,
            "won":         r.won,
            "demo_booked": r.demo_booked,
            "called":      r.called,
            "lost":        r.lost,
            "conversion":  round(r.won / r.total * 100, 1) if r.total else 0,
        }
        for r in rows
    ]


# ── 6. Lead list ───────────────────────────────────────────────────

@router.get("/leads")
def get_leads(
    lead_type:   Optional[str] = Query(None),
    status:      Optional[str] = Query(None),
    area:        Optional[str] = Query(None),
    assigned_to: Optional[str] = Query(None),
    ai_score: Optional[str] = Query(None),
    page:        int = Query(1, ge=1),
    limit:       int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
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

    if ai_score:
        filters.append("ai_score = :ai_score")
        params["ai_score"] = ai_score      

    where  = " AND ".join(filters)
    offset = (page - 1) * limit

    total = db.execute(
        text(f"SELECT COUNT(*) FROM leads WHERE {where}"), params
    ).scalar()

    rows = db.execute(text(f"""
        SELECT id, name, owner_name, phone, email, website,
               area, lead_type, source, score, status,
               notes, assigned_to, last_contacted, created_at, updated_at
        FROM leads WHERE {where}
        ORDER BY score DESC, created_at DESC
        LIMIT :limit OFFSET :offset
    """), {**params, "limit": limit, "offset": offset}).fetchall()

    return {
        "total": total, "page": page, "limit": limit,
        "pages": -(-total // limit),
        "data": [
            {
                "id": r.id, "name": r.name, "owner_name": r.owner_name,
                "phone": r.phone, "email": r.email, "website": r.website,
                "area": r.area, "lead_type": r.lead_type, "source": r.source,
                "score": r.score, "status": r.status, "notes": r.notes,
                "assigned_to": r.assigned_to,
                "last_contacted": r.last_contacted.isoformat() if r.last_contacted else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    }


# ── 7. Search ──────────────────────────────────────────────────────

@router.get("/leads/search")
def search_leads(
    q:     str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    rows = db.execute(text("""
        SELECT id, name, owner_name, phone,
               area, lead_type, status, score
        FROM leads
        WHERE name ILIKE :q OR owner_name ILIKE :q OR area ILIKE :q
        ORDER BY score DESC LIMIT :limit
    """), {"q": f"%{q}%", "limit": limit}).fetchall()

    return [
        {
            "id": r.id, "name": r.name, "owner_name": r.owner_name,
            "phone": r.phone, "area": r.area,
            "lead_type": r.lead_type, "status": r.status, "score": r.score,
        }
        for r in rows
    ]


# ── FEATURE 1: Full lead detail + contact timeline ─────────────────
# Returns complete lead profile plus every event in chronological order.
# Powers the lead detail panel when a rep clicks on a lead.

@router.get("/leads/{lead_id}/timeline")
def get_lead_timeline(
    lead_id: int,
    db: Session = Depends(get_db),
):
    """
    Full lead detail + chronological audit trail.
    Every status change, note, and assignment is recorded here.
    """
    # Full lead profile
    lead = db.execute(text("""
        SELECT
            id, name, owner_name, phone, email, website,
            area, lead_type, source, score, status,
            notes, assigned_to, last_contacted,
            contact_attempts, follow_up_date,
            ai_score, ai_score_reason, ai_scored_at,
            created_at, updated_at
        FROM leads WHERE id = :id
    """), {"id": lead_id}).fetchone()

    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # All events in chronological order
    events = db.execute(text("""
        SELECT id, event_type, from_value, to_value,
               changed_by, note, created_at
        FROM lead_events
        WHERE lead_id = :id
        ORDER BY created_at ASC
    """), {"id": lead_id}).fetchall()

    # All notes in chronological order
    notes = db.execute(text("""
        SELECT id, note, created_by, ai_score,
               ai_score_reason, follow_up_days, signals, created_at
        FROM lead_notes
        WHERE lead_id = :id
        ORDER BY created_at ASC
    """), {"id": lead_id}).fetchall()

    # Merge events and notes into one timeline sorted by time
    timeline = []

    for e in events:
        timeline.append({
            "type":       "event",
            "event_type": e.event_type,
            "from_value": e.from_value,
            "to_value":   e.to_value,
            "changed_by": e.changed_by,
            "note":       e.note,
            "created_at": e.created_at.isoformat(),
        })

    for n in notes:
        timeline.append({
            "type":            "note",
            "event_type":      "note_added",
            "note":            n.note,
            "created_by":      n.created_by,
            "ai_score":        n.ai_score,
            "ai_score_reason": n.ai_score_reason,
            "follow_up_days":  n.follow_up_days,
            "signals":         n.signals or [],
            "created_at":      n.created_at.isoformat(),
        })

    # Sort merged timeline by created_at
    timeline.sort(key=lambda x: x["created_at"])

    return {
        "lead": {
            "id": lead.id, "name": lead.name, "owner_name": lead.owner_name,
            "phone": lead.phone, "email": lead.email, "website": lead.website,
            "area": lead.area, "lead_type": lead.lead_type, "source": lead.source,
            "score": lead.score, "status": lead.status, "notes": lead.notes,
            "assigned_to": lead.assigned_to,
            "last_contacted": lead.last_contacted.isoformat() if lead.last_contacted else None,
            "contact_attempts": lead.contact_attempts or 0,
            "follow_up_date": lead.follow_up_date.isoformat() if lead.follow_up_date else None,
            "ai_score": lead.ai_score,
            "ai_score_reason": lead.ai_score_reason,
            "ai_scored_at": lead.ai_scored_at.isoformat() if lead.ai_scored_at else None,
            "created_at": lead.created_at.isoformat() if lead.created_at else None,
            "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
        },
        "timeline": timeline,
    }


# ── 8. Update status — now writes to lead_events ──────────────────

VALID_STATUSES = {"new", "called", "demo_booked", "won", "lost"}

@router.patch("/leads/{lead_id}/status")
def update_status(
    lead_id: int,
    body:    StatusUpdate,
    db:      Session = Depends(get_db),
):
    if body.status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {VALID_STATUSES}")

    # Get current status before updating
    current = db.execute(
        text("SELECT status, assigned_to FROM leads WHERE id = :id"),
        {"id": lead_id}
    ).fetchone()

    if not current:
        raise HTTPException(404, "Lead not found")

    # Update the lead
    result = db.execute(text("""
        UPDATE leads SET
            status         = :status,
            notes          = COALESCE(:notes, notes),
            assigned_to    = COALESCE(:assigned_to, assigned_to),
            last_contacted = CASE
                WHEN :status IN ('called', 'demo_booked', 'won', 'lost')
                THEN NOW() ELSE last_contacted END,
            updated_at     = NOW()
        WHERE id = :id
        RETURNING id, name, status, assigned_to
    """), {
        "id":          lead_id,
        "status":      body.status,
        "notes":       body.notes,
        "assigned_to": body.assigned_to,
    })
    db.commit()

    row = result.fetchone()

    # Write status change event to timeline
    if current.status != body.status:
        db.execute(text("""
            INSERT INTO lead_events
                (lead_id, event_type, from_value, to_value, changed_by, note)
            VALUES
                (:lead_id, 'status_change', :from_val, :to_val, :changed_by, :note)
        """), {
            "lead_id":    lead_id,
            "from_val":   current.status,
            "to_val":     body.status,
            "changed_by": body.changed_by or body.assigned_to or "system",
            "note":       body.notes,
        })

    # Write assignment event if assigned_to changed
    if body.assigned_to and body.assigned_to != current.assigned_to:
        db.execute(text("""
            INSERT INTO lead_events
                (lead_id, event_type, from_value, to_value, changed_by)
            VALUES
                (:lead_id, 'assigned', :from_val, :to_val, :changed_by)
        """), {
            "lead_id":    lead_id,
            "from_val":   current.assigned_to,
            "to_val":     body.assigned_to,
            "changed_by": body.assigned_to,
        })

    db.commit()

    return {"id": row.id, "name": row.name, "status": row.status}


# ── 9. Update notes ────────────────────────────────────────────────

@router.patch("/leads/{lead_id}/notes")
def update_notes(
    lead_id: int,
    body:    LeadUpdate,
    db:      Session = Depends(get_db),
):
    result = db.execute(text("""
        UPDATE leads SET
            notes       = COALESCE(:notes, notes),
            assigned_to = COALESCE(:assigned_to, assigned_to),
            updated_at  = NOW()
        WHERE id = :id
        RETURNING id, name
    """), {"id": lead_id, "notes": body.notes, "assigned_to": body.assigned_to})
    db.commit()

    row = result.fetchone()
    if not row:
        raise HTTPException(404, "Lead not found")
    return {"id": row.id, "name": row.name, "updated": True}


# ── FEATURE 3: Bulk CSV upload ─────────────────────────────────────
# Rep uploads a CSV with columns: name, phone, area, lead_type, website, email
# System inserts new leads, skips duplicates, rejects rows with no contact.
# Returns a full import report.

REQUIRED_COLUMNS = {"name"}
OPTIONAL_COLUMNS = {"phone", "area", "lead_type", "website", "email", "owner_name"}
VALID_LEAD_TYPES = {"apartment", "agency", "landlord"}


def normalize_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^\w\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


@router.post("/leads/import")
async def import_leads_csv(
    file: UploadFile = File(...),
    db:   Session    = Depends(get_db),
):
    """
    Bulk import leads from a CSV file.

    Expected CSV columns:
        name (required), phone, email, website,
        area, lead_type, owner_name

    Returns an import report showing:
        - inserted: new leads added
        - updated:  existing leads with better contact info
        - duplicates: same name already exists, no better data
        - rejected: no phone AND no website
        - errors: rows that couldn't be parsed
    """
    if not file.filename.endswith((".csv", ".CSV")):
        raise HTTPException(400, "Only CSV files are supported")

    content = await file.read()

    try:
        text_content = content.decode("utf-8")
    except UnicodeDecodeError:
        text_content = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text_content))

    # Validate headers
    if not reader.fieldnames:
        raise HTTPException(400, "CSV file is empty or has no headers")

    headers = {h.lower().strip() for h in reader.fieldnames}
    if "name" not in headers:
        raise HTTPException(400, "CSV must have a 'name' column")

    # Process rows
    inserted   = 0
    updated    = 0
    duplicates = 0
    rejected   = 0
    errors     = []

    for i, row in enumerate(reader, start=2):  # start=2 because row 1 is header
        try:
            # Clean values
            name     = row.get("name", "").strip()
            phone    = row.get("phone", "").strip() or None
            email    = row.get("email", "").strip() or None
            website  = row.get("website", "").strip() or None
            area     = row.get("area", "").strip() or None
            owner    = row.get("owner_name", "").strip() or None
            ltype    = row.get("lead_type", "landlord").strip().lower()

            if not name:
                errors.append({"row": i, "reason": "empty name"})
                continue

            # Reject if no contact method
            if not phone and not website:
                rejected += 1
                continue

            # Validate lead type
            if ltype not in VALID_LEAD_TYPES:
                ltype = "landlord"

            name_norm = normalize_name(name)

            # Check if already exists
            existing = db.execute(
                text("SELECT id, phone, website FROM leads WHERE name_normalized = :n"),
                {"n": name_norm}
            ).fetchone()

            if existing:
                # Only update if we have better contact data
                if (phone and not existing.phone) or (website and not existing.website):
                    db.execute(text("""
                        UPDATE leads SET
                            phone      = COALESCE(:phone,   phone),
                            website    = COALESCE(:website, website),
                            updated_at = NOW()
                        WHERE name_normalized = :n
                    """), {"phone": phone, "website": website, "n": name_norm})
                    db.commit()
                    updated += 1
                else:
                    duplicates += 1
                continue

            # Insert new lead
            db.execute(text("""
                INSERT INTO leads (
                    name, owner_name, phone, email, website,
                    area, lead_type, source, score,
                    status, name_normalized
                ) VALUES (
                    :name, :owner, :phone, :email, :website,
                    :area, :lead_type, 'manual_import', 50,
                    'new', :name_norm
                )
            """), {
                "name":      name,
                "owner":     owner,
                "phone":     phone,
                "email":     email,
                "website":   website,
                "area":      area,
                "lead_type": ltype,
                "name_norm": name_norm,
            })
            db.commit()
            inserted += 1

        except Exception as e:
            errors.append({"row": i, "reason": str(e)[:100]})
            db.rollback()

    return {
        "filename":   file.filename,
        "inserted":   inserted,
        "updated":    updated,
        "duplicates": duplicates,
        "rejected":   rejected,
        "errors":     errors,
        "total_rows": inserted + updated + duplicates + rejected + len(errors),
        "summary":    f"{inserted} new leads added, {duplicates} duplicates skipped, "
                      f"{rejected} rejected (no contact), {len(errors)} errors",
    }