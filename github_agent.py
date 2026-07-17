"""Run one bounded scraper task and persist its status in PostgreSQL."""

import os
import sys
import time
import subprocess
import argparse
import logging
import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [cloud-run-worker] %(message)s"
)
logger = logging.getLogger(__name__)

SCRAPER_TIMEOUT_SECONDS = 720

def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url
    return (
        f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
        f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT', '5432')}"
        f"/{os.getenv('POSTGRES_DB')}"
    )


SCRAPER_COMMANDS = {
    "apartments": ["python", "scraper/spiders/apartments.py"],
    "agencies":   ["python", "scraper/spiders/googlemaps.py"],
    "developers": ["python", "scraper/spiders/developers.py",
                   "--enrich", "--limit", "20"],
}


def build_scraper_command(scraper_type: str, areas: str):
    command = SCRAPER_COMMANDS.get(scraper_type)
    if command is None:
        return None
    if scraper_type in {"apartments", "agencies"}:
        return [*command, "--areas", areas]
    return list(command)


def ensure_command_succeeded(result, stage: str):
    if result.returncode == 0:
        return
    error = result.stderr[-300:] if result.stderr else "Unknown error"
    raise RuntimeError(f"{stage} failed: {error}")


def update_run(run_id: int, data: dict):
    """Persist worker status without depending on a public API callback."""
    values = {
        "id": run_id,
        "status": data["status"],
        "records_found": data.get("records_found"),
        "with_contacts": data.get("with_contacts"),
        "imported": data.get("imported"),
        "updated": data.get("updated"),
        "duplicates": data.get("duplicates"),
        "rejected": data.get("rejected"),
        "error": data.get("error"),
        "duration_seconds": data.get("duration_seconds"),
    }
    conn = psycopg2.connect(get_database_url())
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE scraper_runs SET
                status           = %(status)s,
                records_found    = COALESCE(%(records_found)s, records_found),
                with_contacts    = COALESCE(%(with_contacts)s, with_contacts),
                imported         = COALESCE(%(imported)s, imported),
                updated          = COALESCE(%(updated)s, updated),
                duplicates       = COALESCE(%(duplicates)s, duplicates),
                rejected         = COALESCE(%(rejected)s, rejected),
                error            = COALESCE(%(error)s, error),
                duration_seconds = COALESCE(%(duration_seconds)s, duration_seconds),
                finished_at      = CASE WHEN %(status)s != 'running'
                                   THEN NOW() ELSE finished_at END
            WHERE id = %(id)s
        """, values)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def count_metrics(scraper_type: str) -> dict:
    conn = psycopg2.connect(get_database_url())
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

    start = time.time()

    full_cmd = build_scraper_command(scraper_type, areas)
    if not full_cmd:
        update_run(run_id, {
            "status": "failed",
            "error":  f"Unknown scraper type: {scraper_type}"
        })
        sys.exit(1)

    logger.info(f"Command: {' '.join(full_cmd)}")

    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=SCRAPER_TIMEOUT_SECONDS,
            env={
                **os.environ,
                "POSTGRES_HOST":     os.getenv("POSTGRES_HOST"),
                "POSTGRES_PORT":     os.getenv("POSTGRES_PORT", "5432"),
                "POSTGRES_DB":       os.getenv("POSTGRES_DB"),
                "POSTGRES_USER":     os.getenv("POSTGRES_USER"),
                "POSTGRES_PASSWORD": os.getenv("POSTGRES_PASSWORD"),
            }
        )

        ensure_command_succeeded(result, "Scraper")

        logger.info("Scrape done — promoting to leads...")

        promotion = subprocess.run(
            ["python", "pipeline.py", "--skip-scrape"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ,
                 "POSTGRES_HOST": os.getenv("POSTGRES_HOST"),
                 "POSTGRES_PORT": os.getenv("POSTGRES_PORT", "5432"),
                 "POSTGRES_DB":   os.getenv("POSTGRES_DB"),
                 "POSTGRES_USER": os.getenv("POSTGRES_USER"),
                 "POSTGRES_PASSWORD": os.getenv("POSTGRES_PASSWORD")}
        )
        ensure_command_succeeded(promotion, "Promotion")

        duration = time.time() - start
        metrics  = count_metrics(scraper_type)
        logger.info(f"Done in {duration:.0f}s — {metrics}")

        update_run(run_id, {
            "status":           "success",
            "duration_seconds": duration,
            **metrics,
        })

    except subprocess.TimeoutExpired:
        update_run(run_id, {
            "status": "failed",
            "error":  "Scraping timed out after 12 minutes",
            "duration_seconds": time.time() - start,
        })
        sys.exit(1)
    except Exception as exc:
        logger.exception("Worker failed")
        update_run(run_id, {
            "status": "failed",
            "error": str(exc)[:500],
            "duration_seconds": time.time() - start,
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
