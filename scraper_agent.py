"""
scraper_agent.py — Local scraping agent
Polls Railway for pending scrape jobs, runs them locally on your Mac,
then posts results back to Railway so Lovable can see them.

Keep this running in a terminal whenever the sales team uses Lovable:
  POSTGRES_HOST=localhost python scraper_agent.py
"""

import os
import time
import subprocess
import logging
import requests
import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────
RAILWAY_API = "https://salesintelligence-production-8d1d.up.railway.app"
AGENT_EMAIL = "brian@nyumbazetu.com"
AGENT_PASSWORD = "Nyumba2024"

# Railway DB — scrapers write here, pipeline promotes here
RAILWAY_ENV = {
    **os.environ,
    "POSTGRES_HOST":     "thomas.proxy.rlwy.net",
    "POSTGRES_PORT":     "58979",
    "POSTGRES_DB":       "railway",
    "POSTGRES_USER":     "postgres",
    "POSTGRES_PASSWORD": "aXBqoLntBYTfbmnVyXriHWoaUuSQirZE",
    "REDIS_URL":         "redis://localhost:6379/0",
}

RAILWAY_DB_URL = (
    "postgresql://postgres:aXBqoLntBYTfbmnVyXriHWoaUuSQirZE"
    "@thomas.proxy.rlwy.net:58979/railway"
)

SCRAPER_COMMANDS = {
    "apartments": ["python", "scraper/spiders/apartments.py", "--areas"],
    "agencies":   ["python", "scraper/spiders/googlemaps.py", "--areas"],
    "developers": ["python", "scraper/spiders/developers.py",
                   "--enrich", "--limit", "20", "--areas"],
}

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


# ── Auth ──────────────────────────────────────────────────────────

def get_token() -> str:
    resp = requests.post(f"{RAILWAY_API}/api/auth/login", json={
        "email": AGENT_EMAIL,
        "password": AGENT_PASSWORD,
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


# ── Railway API calls ─────────────────────────────────────────────

def get_pending_runs(token: str) -> list:
    resp = requests.get(
        f"{RAILWAY_API}/api/scraper/runs",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10
    )
    resp.raise_for_status()
    runs = resp.json()
    # Pending = running status with no records_found yet
    return [r for r in runs
            if r["status"] == "running" and r.get("records_found") is None]


def update_run(token: str, run_id: int, data: dict):
    resp = requests.patch(
        f"{RAILWAY_API}/api/scraper/runs/{run_id}",
        headers={"Authorization": f"Bearer {token}"},
        json=data,
        timeout=10
    )
    if resp.status_code not in (200, 204):
        logger.warning(f"Failed to update run {run_id}: {resp.text[:200]}")


# ── Metrics from Railway DB ───────────────────────────────────────

def save_run_records(run_id: int, scraper_type: str):
    """
    After each run, save individual records to scraper_run_records.
    This powers the audit/drill-down view in Lovable.
    outcome: imported / rejected / duplicate
    reason: why it was rejected (no_phone, no_website, duplicate)
    """
    conn = psycopg2.connect(RAILWAY_DB_URL)
    cur = conn.cursor()

    table_map = {
        "apartments": ("apartment_staging",
                       "building_name", "search_area",
                       "contact_phone", "contact_website", "category"),
        "agencies":   ("google_places_leads",
                       "business_name", "area",
                       "phone", "website", "category"),
        "developers": ("developer_staging",
                       "developer_name", "area",
                       "contact_phone", "contact_website", None),
    }

    if scraper_type not in table_map:
        conn.close()
        return

    table, name_col, area_col, phone_col, web_col, cat_col = table_map[scraper_type]
    cat_select = cat_col if cat_col else "NULL"

    try:
        cur.execute(f"""
            SELECT {name_col}, {area_col}, {phone_col}, {web_col}, {cat_select}
            FROM {table}
            WHERE scraped_at >= NOW() - INTERVAL '30 minutes'
        """)
        rows = cur.fetchall()

        for name, area, phone, website, category in rows:
            has_phone   = bool(phone)
            has_website = bool(website)

            # Determine outcome
            if has_phone or has_website:
                # Check if it made it to leads
                cur.execute("""
                    SELECT 1 FROM leads
                    WHERE (name ILIKE %s OR owner_name ILIKE %s)
                    AND promoted_at >= NOW() - INTERVAL '30 minutes'
                    LIMIT 1
                """, (f"%{name}%", f"%{name}%"))
                in_leads = cur.fetchone() is not None
                outcome = "imported" if in_leads else "duplicate"
                reason  = "already exists" if not in_leads else None
            else:
                outcome = "rejected"
                if not has_phone and not has_website:
                    reason = "no phone or website"
                elif not has_phone:
                    reason = "no phone"
                else:
                    reason = "no website"

            cur.execute("""
                INSERT INTO scraper_run_records
                    (run_id, name, area, phone, website, category, outcome, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (run_id, name, area, phone, website, category, outcome, reason))

        conn.commit()
        logger.info(f"Saved {len(rows)} individual records for run {run_id}")

    except Exception as e:
        logger.error(f"save_run_records failed: {e}")
    finally:
        cur.close()
        conn.close()


def count_metrics(scraper_type: str) -> dict:
    """Count what was scraped and promoted in the last 30 minutes."""
    conn = psycopg2.connect(RAILWAY_DB_URL)
    cur = conn.cursor()

    table_map = {
        "apartments": "apartment_staging",
        "agencies":   "google_places_leads",
        "developers": "developer_staging",
    }
    table = table_map.get(scraper_type, "google_places_leads")

    try:
        # Records found
        cur.execute(f"""
            SELECT COUNT(*) FROM {table}
            WHERE scraped_at >= NOW() - INTERVAL '30 minutes'
        """)
        records_found = cur.fetchone()[0]

        # With contacts
        with_contacts = 0
        if scraper_type == "apartments":
            cur.execute("""
                SELECT COUNT(*) FROM apartment_staging
                WHERE scraped_at >= NOW() - INTERVAL '30 minutes'
                AND (contact_phone IS NOT NULL OR contact_website IS NOT NULL)
            """)
            with_contacts = cur.fetchone()[0]
        elif scraper_type == "agencies":
            cur.execute("""
                SELECT COUNT(*) FROM google_places_leads
                WHERE scraped_at >= NOW() - INTERVAL '30 minutes'
                AND phone IS NOT NULL
            """)
            with_contacts = cur.fetchone()[0]
        elif scraper_type == "developers":
            cur.execute("""
                SELECT COUNT(*) FROM developer_staging
                WHERE scraped_at >= NOW() - INTERVAL '30 minutes'
                AND contact_phone IS NOT NULL
            """)
            with_contacts = cur.fetchone()[0]

        # Imported to leads
        cur.execute("""
            SELECT COUNT(*) FROM leads
            WHERE promoted_at >= NOW() - INTERVAL '30 minutes'
        """)
        imported = cur.fetchone()[0]

        return {
            "records_found": records_found,
            "with_contacts": with_contacts,
            "imported":      imported,
            "updated":       0,
            "duplicates":    max(0, records_found - imported),
            "rejected":      max(0, records_found - with_contacts),
        }

    except Exception as e:
        logger.error(f"Metrics count failed: {e}")
        return {
            "records_found": 0, "with_contacts": 0,
            "imported": 0, "updated": 0,
            "duplicates": 0, "rejected": 0,
        }
    finally:
        cur.close()
        conn.close()


# ── Run execution ─────────────────────────────────────────────────

def execute_run(run: dict, token: str):
    run_id       = run["id"]
    scraper_type = run["scraper_type"]
    areas        = run.get("areas") or [run.get("area", "Kilimani")]
    areas_str    = ",".join(areas)

    logger.info(f"[run {run_id}] Starting {scraper_type} for {areas}")
    start = time.time()

    cmd = SCRAPER_COMMANDS.get(scraper_type)
    if not cmd:
        update_run(token, run_id, {
            "status": "failed",
            "error":  f"Unknown scraper type: {scraper_type}"
        })
        return

    full_cmd = cmd + [areas_str]
    logger.info(f"[run {run_id}] Command: {' '.join(full_cmd)}")

    try:
        # Step 1: Run the scraper — writes to Railway DB
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=900,
            env=RAILWAY_ENV,
            cwd=PROJECT_ROOT
        )

        if result.returncode != 0:
            error_msg = result.stderr[-300:] if result.stderr else "Unknown error"
            logger.error(f"[run {run_id}] Scraper failed: {error_msg}")
            update_run(token, run_id, {
                "status":           "failed",
                "error":            error_msg,
                "duration_seconds": time.time() - start,
            })
            return

        logger.info(f"[run {run_id}] Scrape done, promoting to leads...")

        # Step 2: Promote scraped records to leads table on Railway
        promote = subprocess.run(
            ["python", "pipeline.py", "--skip-scrape"],
            capture_output=True,
            text=True,
            timeout=120,
            env=RAILWAY_ENV,
            cwd=PROJECT_ROOT
        )

        if promote.returncode != 0:
            logger.warning(
                f"[run {run_id}] Promotion warning: "
                f"{promote.stderr[-200:] if promote.stderr else 'no output'}"
            )
        else:
            logger.info(f"[run {run_id}] Promotion complete")

        # Step 3: Save individual records for audit view
        save_run_records(run_id, scraper_type)

        # Step 4: Count metrics from Railway DB
        duration = time.time() - start
        metrics  = count_metrics(scraper_type)

        logger.info(f"[run {run_id}] Done in {duration:.0f}s — {metrics}")

        update_run(token, run_id, {
            "status":           "success",
            "duration_seconds": duration,
            **metrics,
        })

    except subprocess.TimeoutExpired:
        update_run(token, run_id, {
            "status":           "failed",
            "error":            "Timed out after 15 minutes",
            "duration_seconds": time.time() - start,
        })
    except Exception as e:
        logger.error(f"[run {run_id}] Exception: {e}")
        update_run(token, run_id, {
            "status":           "failed",
            "error":            str(e)[:300],
            "duration_seconds": time.time() - start,
        })


# ── Main loop ─────────────────────────────────────────────────────

def main():
    logger.info("Scraper agent starting...")
    logger.info(f"Polling {RAILWAY_API} every 30s for pending jobs")
    logger.info(f"Writing scraped data to Railway DB")

    token      = None
    token_time = 0

    while True:
        try:
            # Refresh token every 6 hours
            if time.time() - token_time > 21600:
                token      = get_token()
                token_time = time.time()
                logger.info("Token refreshed")

            pending = get_pending_runs(token)

            if pending:
                logger.info(f"Found {len(pending)} pending job(s)")
                for run in pending:
                    execute_run(run, token)
            else:
                logger.debug("No pending jobs — sleeping 30s")

        except Exception as e:
            logger.error(f"Agent loop error: {e}")

        time.sleep(30)


if __name__ == "__main__":
    main()
