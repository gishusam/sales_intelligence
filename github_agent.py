"""
github_agent.py — Runs a single scrape job in GitHub Actions.
Called by the workflow with --run-id, --scraper-type, --areas.
Posts results back to Railway when done.
"""

import os
import sys
import time
import subprocess
import argparse
import logging
import psycopg2
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [github-agent] %(message)s"
)
logger = logging.getLogger(__name__)

RAILWAY_API    = os.getenv("RAILWAY_API", "https://salesintelligence-production-8d1d.up.railway.app")
AGENT_EMAIL    = os.getenv("AGENT_EMAIL", "brian@nyumbazetu.com")
AGENT_PASSWORD = os.getenv("AGENT_PASSWORD", "Nyumba2024")

RAILWAY_DB_URL = (
    f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
    f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT', '5432')}"
    f"/{os.getenv('POSTGRES_DB')}"
)

SCRAPER_COMMANDS = {
    "apartments": ["python", "scraper/spiders/apartments.py", "--areas"],
    "agencies":   ["python", "scraper/spiders/googlemaps.py", "--areas"],
    "developers": ["python", "scraper/spiders/developers.py",
                   "--enrich", "--limit", "20", "--areas"],
}


def get_token() -> str:
    resp = requests.post(f"{RAILWAY_API}/api/auth/login", json={
        "email":    AGENT_EMAIL,
        "password": AGENT_PASSWORD,
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def update_run(token: str, run_id: int, data: dict):
    resp = requests.patch(
        f"{RAILWAY_API}/api/scraper/runs/{run_id}",
        headers={"Authorization": f"Bearer {token}"},
        json=data,
        timeout=10
    )
    if resp.status_code not in (200, 204):
        logger.warning(f"Update failed: {resp.text[:200]}")


def count_metrics(scraper_type: str) -> dict:
    conn = psycopg2.connect(RAILWAY_DB_URL)
    cur  = conn.cursor()

    table_map = {
        "apartments": "apartment_staging",
        "agencies":   "google_places_leads",
        "developers": "developer_staging",
    }
    table = table_map.get(scraper_type, "google_places_leads")

    try:
        cur.execute(f"""
            SELECT COUNT(*) FROM {table}
            WHERE scraped_at >= NOW() - INTERVAL '30 minutes'
        """)
        records_found = cur.fetchone()[0]

        with_contacts = 0
        if scraper_type == "agencies":
            cur.execute("""
                SELECT COUNT(*) FROM google_places_leads
                WHERE scraped_at >= NOW() - INTERVAL '30 minutes'
                AND phone IS NOT NULL
            """)
            with_contacts = cur.fetchone()[0]
        elif scraper_type == "apartments":
            cur.execute("""
                SELECT COUNT(*) FROM apartment_staging
                WHERE scraped_at >= NOW() - INTERVAL '30 minutes'
                AND (contact_phone IS NOT NULL OR contact_website IS NOT NULL)
            """)
            with_contacts = cur.fetchone()[0]

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
        logger.error(f"Metrics failed: {e}")
        return {
            "records_found": 0, "with_contacts": 0,
            "imported": 0, "updated": 0,
            "duplicates": 0, "rejected": 0,
        }
    finally:
        cur.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id",       type=int,  required=True)
    parser.add_argument("--scraper-type", type=str,  required=True)
    parser.add_argument("--areas",        type=str,  required=True)
    args = parser.parse_args()

    run_id       = args.run_id
    scraper_type = args.scraper_type
    areas        = args.areas

    logger.info(f"Starting run {run_id}: {scraper_type} for {areas}")

    token = get_token()
    start = time.time()

    cmd = SCRAPER_COMMANDS.get(scraper_type)
    if not cmd:
        update_run(token, run_id, {
            "status": "failed",
            "error":  f"Unknown scraper type: {scraper_type}"
        })
        sys.exit(1)

    full_cmd = cmd + [areas]
    logger.info(f"Command: {' '.join(full_cmd)}")

    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=900,
            env={
                **os.environ,
                "POSTGRES_HOST":     os.getenv("POSTGRES_HOST"),
                "POSTGRES_PORT":     os.getenv("POSTGRES_PORT", "5432"),
                "POSTGRES_DB":       os.getenv("POSTGRES_DB"),
                "POSTGRES_USER":     os.getenv("POSTGRES_USER"),
                "POSTGRES_PASSWORD": os.getenv("POSTGRES_PASSWORD"),
            }
        )

        if result.returncode != 0:
            error = result.stderr[-300:] if result.stderr else "Unknown error"
            logger.error(f"Scraper failed: {error}")
            update_run(token, run_id, {
                "status":           "failed",
                "error":            error,
                "duration_seconds": time.time() - start,
            })
            sys.exit(1)

        logger.info("Scrape done — promoting to leads...")

        subprocess.run(
            ["python", "pipeline.py", "--skip-scrape"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ,
                 "POSTGRES_HOST": os.getenv("POSTGRES_HOST"),
                 "POSTGRES_PORT": os.getenv("POSTGRES_PORT", "5432"),
                 "POSTGRES_DB":   os.getenv("POSTGRES_DB"),
                 "POSTGRES_USER": os.getenv("POSTGRES_USER"),
                 "POSTGRES_PASSWORD": os.getenv("POSTGRES_PASSWORD")}
        )

        duration = time.time() - start
        metrics  = count_metrics(scraper_type)
        logger.info(f"Done in {duration:.0f}s — {metrics}")

        update_run(token, run_id, {
            "status":           "success",
            "duration_seconds": duration,
            **metrics,
        })

    except subprocess.TimeoutExpired:
        update_run(token, run_id, {
            "status": "failed",
            "error":  "Timed out after 15 minutes",
            "duration_seconds": time.time() - start,
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
