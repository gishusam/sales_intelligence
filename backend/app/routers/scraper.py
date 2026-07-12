"""
scraper.py — Scrape run management endpoints
Powers the Lead Acquisition Pipeline page in Lovable.

POST /api/scraper/run       — trigger a scrape in the background
GET  /api/scraper/runs      — list all runs (history table)
GET  /api/scraper/runs/{id} — poll a specific run (live status)
"""

import subprocess
import threading
import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, CurrentUser

router = APIRouter(prefix="/api/scraper", tags=["scraper"])
logger = logging.getLogger(__name__)

VALID_SCRAPERS = {"apartments", "agencies", "developers"}

# Map frontend scraper_type to pipeline.py arguments
SCRAPER_COMMANDS = {
    "apartments": ["python", "scraper/spiders/apartments.py", "--areas"],
    "agencies":   ["python", "scraper/spiders/googlemaps.py", "--areas"],
    "developers": ["python", "scraper/spiders/developers.py", "--enrich", "--areas"],
}


class RunRequest(BaseModel):
    scraper_type: str
    areas: List[str]


def run_scraper_background(run_id: int, scraper_type: str,
                            areas: List[str], db_url: str):
    """
    Runs in a background thread.
    Executes the scraper, then updates scraper_runs with results.
    """
    import psycopg2
    import time

    start = time.time()
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    try:
        areas_str = ",".join(areas)
        cmd = SCRAPER_COMMANDS.get(scraper_type, [])
        if not cmd:
            raise ValueError(f"Unknown scraper type: {scraper_type}")

        full_cmd = cmd + [areas_str]
        logger.info(f"[run {run_id}] Starting: {' '.join(full_cmd)}")

        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=900,  # 15 min max
            env={
                **__import__('os').environ,
                "POSTGRES_HOST": __import__('os').getenv("POSTGRES_HOST", "localhost"),
            }
        )

        duration = time.time() - start

        if result.returncode != 0:
            raise RuntimeError(result.stderr[:500] if result.stderr else "Unknown error")

        # Parse metrics from stdout
        records_found, with_contacts, imported, updated, duplicates, rejected = \
            parse_metrics(result.stdout, scraper_type, cur)

        cur.execute("""
            UPDATE scraper_runs SET
                status           = 'success',
                records_found    = %s,
                with_contacts    = %s,
                imported         = %s,
                updated          = %s,
                duplicates       = %s,
                rejected         = %s,
                finished_at      = NOW(),
                duration_seconds = %s
            WHERE id = %s
        """, (records_found, with_contacts, imported, updated,
              duplicates, rejected, duration, run_id))
        conn.commit()
        logger.info(f"[run {run_id}] Done — found:{records_found} "
                    f"imported:{imported} duration:{duration:.0f}s")

    except Exception as e:
        logger.error(f"[run {run_id}] Failed: {e}")
        cur.execute("""
            UPDATE scraper_runs SET
                status      = 'failed',
                error       = %s,
                finished_at = NOW(),
                duration_seconds = %s
            WHERE id = %s
        """, (str(e)[:500], time.time() - start, run_id))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def parse_metrics(stdout: str, scraper_type: str, cur) -> tuple:
    """
    Extract metrics from scraper stdout output.
    Falls back to querying the DB directly for counts.
    """
    import re

    # Try parsing from stdout first
    found = _extract_int(stdout, r'(\d+)\s+(?:cards?|buildings?|records?)\s+(?:found|discovered)')
    with_c = _extract_int(stdout, r'(\d+)\s+(?:with\s+)?(?:phone|contact)')
    saved  = _extract_int(stdout, r'(?:saved|stored)\s+(\d+)')

    # If stdout parsing fails, count from DB directly
    table_map = {
        "apartments": "apartment_staging",
        "agencies":   "google_places_leads",
        "developers": "developer_staging",
    }
    table = table_map.get(scraper_type)

    if table and found is None:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE scraped_at >= NOW() - INTERVAL '2 hours'")
        found = cur.fetchone()[0]

    if table and with_c is None and scraper_type == "apartments":
        cur.execute("""
            SELECT COUNT(*) FROM apartment_staging
            WHERE scraped_at >= NOW() - INTERVAL '2 hours'
            AND (contact_phone IS NOT NULL OR contact_website IS NOT NULL)
        """)
        with_c = cur.fetchone()[0]

    # Count what was promoted to leads table
    cur.execute("""
        SELECT COUNT(*) FROM leads
        WHERE promoted_at >= NOW() - INTERVAL '2 hours'
    """)
    imported = cur.fetchone()[0]

    found     = found or 0
    with_c    = with_c or 0
    imported  = imported or 0
    updated   = 0
    duplicates = max(0, found - imported)
    rejected  = max(0, found - with_c)

    return found, with_c, imported, updated, duplicates, rejected


def _extract_int(text: str, pattern: str) -> Optional[int]:
    import re
    m = re.search(pattern, text, re.IGNORECASE)
    return int(m.group(1)) if m else None


# ── Endpoints ─────────────────────────────────────────────────────

@router.post("/run")
def trigger_run(
    body: RunRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Kick off a scrape in the background. Returns immediately."""
    if body.scraper_type not in VALID_SCRAPERS:
        raise HTTPException(400, f"Invalid scraper type. Must be one of: {VALID_SCRAPERS}")
    if not body.areas:
        raise HTTPException(400, "At least one area is required")

    # Create the run record
    row = db.execute(text("""
        INSERT INTO scraper_runs (scraper_type, areas, status, started_by)
        VALUES (:scraper_type, :areas, 'running', :started_by)
        RETURNING id
    """), {
        "scraper_type": body.scraper_type,
        "areas":        body.areas,
        "started_by":   user.name,
    }).fetchone()
    db.commit()

    run_id = row.id

    # Build DB URL for background thread
    from app.config import settings
    db_url = settings.database_url

    # Trigger GitHub Actions workflow
    import os, httpx
    github_token = os.getenv("GITHUB_TOKEN", "")
    github_repo  = os.getenv("GITHUB_REPO", "")  # e.g. "samwelngugi/nyumba-zetu-intelligence"

    if github_token and github_repo:
        try:
            resp = httpx.post(
                f"https://api.github.com/repos/{github_repo}/dispatches",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept":        "application/vnd.github.v3+json",
                },
                json={
                    "event_type": "run_scraper",
                    "client_payload": {
                        "run_id":       run_id,
                        "scraper_type": body.scraper_type,
                        "areas":        ",".join(body.areas),
                    }
                },
                timeout=10
            )
            if resp.status_code == 204:
                logger.info(f"GitHub Actions triggered for run {run_id}")
            else:
                logger.warning(f"GitHub dispatch failed: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"GitHub dispatch error: {e}")
    else:
        logger.warning("GITHUB_TOKEN or GITHUB_REPO not set — scraper won't run")

    return {"run_id": run_id, "status": "running", "message": f"{body.scraper_type} scraper started", "areas": body.areas}


@router.get("/runs")
def get_runs(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """List all scrape runs — powers the history table."""
    rows = db.execute(text("""
        SELECT
            id, scraper_type, areas, status,
            records_found, with_contacts, imported,
            updated, duplicates, rejected, error,
            started_at, finished_at, duration_seconds,
            started_by
        FROM scraper_runs
        ORDER BY started_at DESC
        LIMIT 50
    """)).fetchall()

    return [_format_run(r) for r in rows]


@router.get("/runs/{run_id}")
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Poll a specific run — used for live status updates."""
    row = db.execute(text("""
        SELECT
            id, scraper_type, areas, status,
            records_found, with_contacts, imported,
            updated, duplicates, rejected, error,
            started_at, finished_at, duration_seconds,
            started_by
        FROM scraper_runs WHERE id = :id
    """), {"id": run_id}).fetchone()

    if not row:
        raise HTTPException(404, "Run not found")

    return _format_run(row)


def _format_run(r) -> dict:
    return {
        "id":               r.id,
        "scraper_type":     r.scraper_type,
        "areas":            r.areas or [],
        "area":             ", ".join(r.areas) if r.areas else None,
        "status":           r.status,
        "records_found":    r.records_found,
        "with_contacts":    r.with_contacts,
        "imported":         r.imported,
        "updated":          r.updated,
        "duplicates":       r.duplicates,
        "rejected":         r.rejected,
        "error":            r.error,
        "started_at":       r.started_at.isoformat() if r.started_at else None,
        "finished_at":      r.finished_at.isoformat() if r.finished_at else None,
        "created_at":       r.started_at.isoformat() if r.started_at else None,
        "duration_seconds": r.duration_seconds,
    }


# ── Agent callback — Mac posts results back to Railway ────────────

class RunUpdate(BaseModel):
    status: str
    records_found: Optional[int] = None
    with_contacts: Optional[int] = None
    imported: Optional[int] = None
    updated: Optional[int] = None
    duplicates: Optional[int] = None
    rejected: Optional[int] = None
    error: Optional[str] = None
    duration_seconds: Optional[float] = None


@router.patch("/runs/{run_id}")
def update_run(
    run_id: int,
    body: RunUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Called by the local scraper agent to post results back."""
    db.execute(text("""
        UPDATE scraper_runs SET
            status           = :status,
            records_found    = COALESCE(:records_found, records_found),
            with_contacts    = COALESCE(:with_contacts, with_contacts),
            imported         = COALESCE(:imported, imported),
            updated          = COALESCE(:updated, updated),
            duplicates       = COALESCE(:duplicates, duplicates),
            rejected         = COALESCE(:rejected, rejected),
            error            = COALESCE(:error, error),
            duration_seconds = COALESCE(:duration_seconds, duration_seconds),
            finished_at      = CASE WHEN :status != 'running'
                               THEN NOW() ELSE finished_at END
        WHERE id = :id
    """), {"id": run_id, **body.dict()})
    db.commit()
    return {"id": run_id, "status": body.status}


@router.get("/runs/{run_id}/records")
def get_run_records(
    run_id: int,
    outcome: Optional[str] = None,  # imported / rejected / duplicate
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Returns individual records from a scrape run.
    Powers the audit drill-down in Lovable.

    outcome filter:
      imported  — records that made it into leads table
      rejected  — records dropped (no phone, no website)
      duplicate — records found but already in leads
    """
    filters = ["run_id = :run_id"]
    params  = {"run_id": run_id}

    if outcome:
        filters.append("outcome = :outcome")
        params["outcome"] = outcome

    where = " AND ".join(filters)

    rows = db.execute(text(f"""
        SELECT id, name, area, phone, website,
               category, outcome, reason, created_at
        FROM scraper_run_records
        WHERE {where}
        ORDER BY outcome, name
    """), params).fetchall()

    # Summary counts
    all_rows = db.execute(text("""
        SELECT outcome, COUNT(*) as count
        FROM scraper_run_records
        WHERE run_id = :run_id
        GROUP BY outcome
    """), {"run_id": run_id}).fetchall()

    summary = {r.outcome: r.count for r in all_rows}

    return {
        "run_id":  run_id,
        "summary": {
            "imported":  summary.get("imported",  0),
            "rejected":  summary.get("rejected",  0),
            "duplicate": summary.get("duplicate", 0),
            "total":     sum(summary.values()),
        },
        "records": [
            {
                "id":       r.id,
                "name":     r.name,
                "area":     r.area,
                "phone":    r.phone,
                "website":  r.website,
                "category": r.category,
                "outcome":  r.outcome,
                "reason":   r.reason,
            }
            for r in rows
        ]
    }
