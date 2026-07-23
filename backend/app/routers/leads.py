# backend/app/routers/leads.py
# Full leads router with:
#   - All original 9 endpoints
#   - Feature 1: Lead detail + contact timeline (lead_events)
#   - Feature 2: Lead type summary strip
#   - Feature 3: Bulk CSV upload with import report

import io
import csv
import re
import openpyxl
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, CurrentUser

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


# ── 7. My leads ────────────────────────────────────────────────────

@router.get("/leads/mine")
def get_my_leads(
    page:  int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db:    Session = Depends(get_db),
    user:  CurrentUser = Depends(get_current_user),
):
    """
    Returns leads assigned to the current user.
    Matches assigned_to against both the user's name and their id (as a string)
    since the frontend may send either form.
    """
    offset = (page - 1) * limit

    total = db.execute(text("""
        SELECT COUNT(*) FROM leads
        WHERE assigned_to = :name OR assigned_to = :uid
    """), {"name": user.name, "uid": str(user.id)}).scalar()

    rows = db.execute(text("""
        SELECT id, name, owner_name, phone, email, website,
               area, lead_type, source, score, status,
               notes, assigned_to, last_contacted, created_at, updated_at
        FROM leads
        WHERE assigned_to = :name OR assigned_to = :uid
        ORDER BY score DESC, created_at DESC
        LIMIT :limit OFFSET :offset
    """), {"name": user.name, "uid": str(user.id), "limit": limit, "offset": offset}).fetchall()

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


# ── 8. Search ──────────────────────────────────────────────────────

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



@router.patch("/leads/{lead_id}/assign")
def assign_lead(
    lead_id: int,
    db:      Session = Depends(get_db),
    user:    CurrentUser = Depends(get_current_user),
):
    """
    Assign a lead to the currently logged-in user.
    Called when rep clicks "Assign to me" button.
    Uses JWT identity — no body needed.
    """
    result = db.execute(text("""
        UPDATE leads SET
            assigned_to = :user_name,
            updated_at  = NOW()
        WHERE id = :id
        RETURNING id, name, assigned_to
    """), {"id": lead_id, "user_name": user.name})
    db.commit()

    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Log to events
    db.execute(text("""
        INSERT INTO lead_events (
            lead_id, event_type, to_value,
            changed_by, note, created_at
        ) VALUES (
            :lead_id, 'assigned', :to_value,
            :changed_by, :note, NOW()
        )
    """), {
        "lead_id":    lead_id,
        "to_value":   user.name,
        "changed_by": user.name,
        "note":       f"Lead assigned to {user.name}",
    })
    db.commit()

    return {
        "id":          row.id,
        "name":        row.name,
        "assigned_to": row.assigned_to,
        "message":     f"Lead assigned to {user.name}",
    }


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
VALID_LEAD_TYPES = {"apartment", "agency", "landlord", "developer"}

SCORE_DISPLAY = {
    "LOW_HANGING_FRUIT": "Low Hanging Fruit",
    "WARM_PROSPECT":     "Warm Prospect",
    "EXECUTIVE_LEAD":    "Executive Lead",
    "NURTURE":           "Nurture",
    "NOT_QUALIFIED":     "Not Qualified",
}


def normalize_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^\w\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


# Map from possible column names in uploaded files → our standard field names
COLUMN_ALIASES = {
    # name
    "name":                        "name",
    "apartment name/company name": "name",
    "company name":                "name",
    "apartment name":              "name",
    "business name":               "name",
    "agency name":                 "name",
    "property name":               "name",

    # owner_name
    "owner_name":                  "owner_name",
    "owner":                       "owner_name",
    "point of contact name":       "owner_name",
    "contact name":                "owner_name",
    "contact person":              "owner_name",

    # phone
    "phone":                       "phone",
    "phone number":                "phone",
    "phone number -":              "phone",
    "phone number decision maker": "phone",
    "mobile":                      "phone",
    "tel":                         "phone",
    "telephone":                   "phone",
    "contact":                     "phone",

    # email
    "email":                       "email",
    "email address":               "email",
    "e-mail":                      "email",

    # area
    "area":                        "area",
    "location":                    "area",
    "zone":                        "area",
    "region":                      "area",
    "address":                     "area",

    # lead_type
    "lead_type":                   "lead_type",
    "type":                        "lead_type",
    "role":                        "lead_type",
    "category":                    "lead_type",

    # website
    "website":                     "website",
    "web":                         "website",
    "url":                         "website",
    "website url":                 "website",

    # notes
    "notes":                       "notes",
    "comments":                    "notes",
    "comment":                     "notes",
    "hubspot status":              "notes",
    "status":                      "notes",
}


def normalize_header(h: str) -> str:
    """Map any column name variant to our standard field name."""
    cleaned = h.strip().lower().rstrip("-").strip()
    return COLUMN_ALIASES.get(cleaned, cleaned)


def parse_csv(content: bytes) -> list[dict]:
    """
    Parse CSV or TSV file content into list of row dicts.
    Handles: BOM, tab-separated, semicolon-separated,
    varied column names, extra spaces.
    """
    try:
        text_content = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text_content = content.decode("latin-1")

    # Auto-detect delimiter — tab, semicolon, or comma
    first_line = text_content.split("\n")[0]
    if "\t" in first_line:
        delimiter = "\t"
    elif ";" in first_line:
        delimiter = ";"
    else:
        delimiter = ","

    reader = csv.DictReader(io.StringIO(text_content), delimiter=delimiter)
    raw_rows = list(reader)
    raw_fieldnames = reader.fieldnames or []

    # Map raw headers to standard names
    header_map = {f: normalize_header(f) for f in raw_fieldnames}

    normalized_rows = []
    for row in raw_rows:
        normalized = {}
        for raw_col, std_col in header_map.items():
            val = row.get(raw_col, "")
            val = str(val).strip() if val else ""
            # Keep the last value if there are duplicate mapped columns
            if val:
                normalized[std_col] = val
        normalized_rows.append(normalized)

    normalized_fieldnames = list(set(header_map.values()))
    return normalized_rows, normalized_fieldnames


def parse_excel(content: bytes) -> tuple[list[dict], list[str]]:
    """Parse Excel (.xlsx) file into list of row dicts."""
    wb   = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    ws   = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], []
    headers  = [str(h).strip().lower() if h else "" for h in rows[0]]
    data     = []
    for row in rows[1:]:
        if all(cell is None for cell in row):
            continue
        data.append({
            headers[i]: str(cell).strip() if cell is not None else ""
            for i, cell in enumerate(row)
            if i < len(headers)
        })
    return data, headers




@router.get("/leads/outreach")
def get_outreach_leads(
    lead_type:  str = Query(None),
    filter_by:  str = Query("all"),  # all / emailed / not_emailed / replied
    area:       str = Query(None),
    page:       int = Query(1, ge=1),
    limit:      int = Query(20, ge=1, le=100),
    db:         Session = Depends(get_db),
    user:       CurrentUser = Depends(get_current_user),
):
    """
    Cold outreach view — leads segmented by email status.
    filter_by:
      all         — all leads with phone or email
      emailed     — cold email sent (email_sent_at IS NOT NULL)
      not_emailed — never contacted by email (email_sent_at IS NULL)

    Powers the cold outreach filter tabs on Apartments/Agencies pages.
    """
    filters = ["(phone IS NOT NULL OR email IS NOT NULL)"]
    params  = {}

    if lead_type:
        filters.append("lead_type = :lead_type")
        params["lead_type"] = lead_type

    if area:
        filters.append("area ILIKE :area")
        params["area"] = f"%{area}%"

    if filter_by == "emailed":
        filters.append("email_sent_at IS NOT NULL")
    elif filter_by == "not_emailed":
        filters.append("email_sent_at IS NULL")

    where  = " AND ".join(filters)
    offset = (page - 1) * limit

    total = db.execute(
        text(f"SELECT COUNT(*) FROM leads WHERE {where}"), params
    ).scalar()

    rows = db.execute(text(f"""
        SELECT
            l.id, l.name, l.owner_name, l.phone, l.email,
            l.website, l.area, l.lead_type, l.score,
            l.status, l.assigned_to, l.ai_score,
            l.last_contacted, l.email_sent_at, l.follow_up_date,
            l.contact_attempts,
            -- Last email sent
            (SELECT e.sent_at FROM email_outreach e
             WHERE e.lead_id = l.id AND e.status = 'sent'
             ORDER BY e.sent_at DESC LIMIT 1) AS last_email_sent_at,
            (SELECT e.email_type FROM email_outreach e
             WHERE e.lead_id = l.id AND e.status = 'sent'
             ORDER BY e.sent_at DESC LIMIT 1) AS last_email_type,
            (SELECT u.name FROM email_outreach e
             JOIN users u ON u.id = e.sent_by
             WHERE e.lead_id = l.id AND e.status = 'sent'
             ORDER BY e.sent_at DESC LIMIT 1) AS last_email_by
        FROM leads l
        WHERE {where}
        ORDER BY
            CASE WHEN l.email_sent_at IS NOT NULL THEN 0 ELSE 1 END,
            l.email_sent_at DESC NULLS LAST,
            l.score DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """), {**params, "limit": limit, "offset": offset}).fetchall()

    # Summary counts for the filter tabs
    counts = db.execute(text(f"""
        SELECT
            COUNT(*)                                    AS total,
            COUNT(*) FILTER (WHERE email_sent_at IS NOT NULL) AS emailed,
            COUNT(*) FILTER (WHERE email_sent_at IS NULL)     AS not_emailed
        FROM leads
        WHERE {where.replace("email_sent_at IS NOT NULL", "1=1")
                     .replace("email_sent_at IS NULL", "1=1")}
    """), {k: v for k, v in params.items()
           if k not in ("email_sent_at",)}).fetchone()

    return {
        "total":    total,
        "page":     page,
        "limit":    limit,
        "pages":    -(-total // limit) if total else 0,
        "filter":   filter_by,
        "counts": {
            "all":         counts.total,
            "emailed":     counts.emailed,
            "not_emailed": counts.not_emailed,
        },
        "data": [
            {
                "id":               r.id,
                "name":             r.name,
                "owner_name":       r.owner_name,
                "phone":            r.phone,
                "email":            r.email,
                "website":          r.website,
                "area":             r.area,
                "lead_type":        r.lead_type,
                "score":            r.score,
                "status":           r.status,
                "assigned_to":      r.assigned_to,
                "ai_score":         r.ai_score,
                "last_contacted":   r.last_contacted.isoformat() if r.last_contacted else None,
                "email_sent_at":    r.email_sent_at.isoformat() if r.email_sent_at else None,
                "follow_up_date":   r.follow_up_date.isoformat() if r.follow_up_date else None,
                "contact_attempts": r.contact_attempts or 0,
                "last_email_sent_at": r.last_email_sent_at.isoformat() if r.last_email_sent_at else None,
                "last_email_type":  r.last_email_type,
                "last_email_by":    r.last_email_by,
                "email_status":     "emailed" if r.email_sent_at else "not_emailed",
            }
            for r in rows
        ],
    }


@router.post("/leads/import")
async def import_leads(
    file: UploadFile = File(...),
    db:   Session    = Depends(get_db),
):
    """
    Bulk import leads from CSV or Excel (.xlsx) file.

    Expected columns:
        name (required), phone, email, website,
        area, lead_type, owner_name

    Returns a detailed audit report showing exactly which records
    were inserted, updated, duplicated, or rejected — by name.
    """
    filename = file.filename.lower()
    content  = await file.read()

    # ── Parse file ─────────────────────────────────────────────────
    if filename.endswith(".csv"):
        try:
            rows, fieldnames = parse_csv(content)
        except Exception as e:
            raise HTTPException(400, f"Could not parse CSV: {e}")

    elif filename.endswith((".xlsx", ".xls")):
        try:
            rows, fieldnames = parse_excel(content)
        except Exception as e:
            raise HTTPException(400, f"Could not parse Excel file: {e}")
    else:
        raise HTTPException(400, "Only CSV (.csv) or Excel (.xlsx) files are supported")

    if not fieldnames:
        raise HTTPException(400, "File is empty or has no headers")

    headers = {h.lower().strip() for h in fieldnames if h}
    if "name" not in headers:
        raise HTTPException(400, "File must have a 'name' column")

    # ── Process rows ───────────────────────────────────────────────
    inserted_records   = []   # list of {row, name, area, phone}
    updated_records    = []   # list of {row, name, what_changed}
    duplicate_records  = []   # list of {row, name, reason}
    rejected_records   = []   # list of {row, name, reason}
    error_records      = []   # list of {row, reason}

    for i, row in enumerate(rows, start=2):
        try:
            name    = str(row.get("name", "") or "").strip()
            phone   = str(row.get("phone", "") or "").strip() or None
            email   = str(row.get("email", "") or "").strip() or None
            website = str(row.get("website", "") or "").strip() or None
            area    = str(row.get("area", "") or "").strip() or None
            owner   = str(row.get("owner_name", "") or "").strip() or None
            ltype   = str(row.get("lead_type", "") or "").strip().lower()

            if not name:
                error_records.append({"row": i, "name": "—", "reason": "Empty name field"})
                continue

            # Reject if no contact method at all
            if not phone and not website and not email:
                rejected_records.append({
                    "row":    i,
                    "name":   name,
                    "reason": "No phone, email, or website provided"
                })
                continue

            # Validate and default lead type
            if ltype not in VALID_LEAD_TYPES:
                ltype = "agency"

            name_norm = normalize_name(name)
            if not name_norm:
                error_records.append({"row": i, "name": name, "reason": "Name normalizes to empty"})
                continue

            # Check if already exists
            existing = db.execute(
                text("SELECT id, phone, website, email FROM leads WHERE LOWER(name) = :n"),
                {"n": name_norm}
            ).fetchone()

            if existing:
                # Update if we have better contact data
                changes = []
                if phone and not existing.phone:
                    changes.append("added phone")
                if website and not existing.website:
                    changes.append("added website")
                if email and not existing.email:
                    changes.append("added email")

                if changes:
                    db.execute(text("""
                        UPDATE leads SET
                            phone      = COALESCE(:phone,   phone),
                            website    = COALESCE(:website, website),
                            email      = COALESCE(:email,   email),
                            updated_at = NOW()
                        WHERE LOWER(name) = :n
                    """), {"phone": phone, "website": website, "email": email, "n": name_norm})
                    db.commit()
                    updated_records.append({
                        "row":          i,
                        "name":         name,
                        "what_changed": ", ".join(changes),
                    })
                else:
                    duplicate_records.append({
                        "row":    i,
                        "name":   name,
                        "reason": "Already exists with same or better contact info"
                    })
                continue

            # Insert new lead
            db.execute(text("""
                INSERT INTO leads (
                    name, owner_name, phone, email, website,
                    area, lead_type, source, score,
                    status
                ) VALUES (
                    :name, :owner, :phone, :email, :website,
                    :area, :lead_type, 'bulk_upload', 40,
                    'new'
                )
            """), {
                "name":      name,
                "owner":     owner,
                "phone":     phone,
                "email":     email,
                "website":   website,
                "area":      area,
                "lead_type": ltype,
            })
            db.commit()

            inserted_records.append({
                "row":      i,
                "name":     name,
                "area":     area or "—",
                "phone":    phone or "—",
                "lead_type": ltype,
            })

        except Exception as e:
            db.rollback()
            error_records.append({"row": i, "name": row.get("name", "—"), "reason": str(e)[:120]})

    # ── Build audit report ─────────────────────────────────────────
    total_rows = len(inserted_records) + len(updated_records) + \
                 len(duplicate_records) + len(rejected_records) + len(error_records)

    return {
        "filename":   file.filename,
        "total_rows": total_rows,

        # Summary counts
        "inserted":   len(inserted_records),
        "updated":    len(updated_records),
        "duplicates": len(duplicate_records),
        "rejected":   len(rejected_records),
        "errors":     len(error_records),

        "summary": (
            f"{len(inserted_records)} new leads added, "
            f"{len(updated_records)} updated with better contact info, "
            f"{len(duplicate_records)} duplicates skipped, "
            f"{len(rejected_records)} rejected (no contact), "
            f"{len(error_records)} errors"
        ),

        # Detailed audit — every record by name
        "audit": {
            "inserted":  inserted_records,
            "updated":   updated_records,
            "duplicates": duplicate_records,
            "rejected":  rejected_records,
            "errors":    error_records,
        }
    }


