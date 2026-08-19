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


def build_scraper_command(scraper_type: str, area_id: str | None, run_id: int | None = None):
    command = SCRAPER_COMMANDS.get(scraper_type)
    if command is None:
        return None
    if scraper_type in {"apartments", "agencies"}:
        if not area_id:
            return None

        full_command = [*command, "--area-id", area_id]

        if scraper_type in {"agencies", "apartments"} and run_id is not None:
            full_command.extend(["--run-id", str(run_id)])

        return full_command
    return list(command)


def ensure_command_succeeded(result, stage: str):
    if result.returncode == 0:
        return
    error = result.stderr[-300:] if result.stderr else "Unknown error"
    raise RuntimeError(f"{stage} failed: {error}")


def ensure_scrape_produced_records(metrics: dict):
    """A clean process exit with no scraped records is not a successful run."""
    if metrics.get("records_found", 0) <= 0:
        raise RuntimeError("Scraper completed with zero records")


def log_subprocess_output(result, stage: str):
    """Keep child-process evidence in Cloud Logging even when it exits cleanly."""
    if result.stdout:
        logger.info("%s stdout:\n%s", stage, result.stdout[-12000:])
    if result.stderr:
        logger.info("%s stderr:\n%s", stage, result.stderr[-12000:])


def connect_database(max_attempts: int = 3, base_delay_seconds: float = 1.0):
    """Connect for status persistence, retrying transient capacity failures."""
    for attempt in range(1, max_attempts + 1):
        try:
            return psycopg2.connect(get_database_url())
        except psycopg2.OperationalError:
            if attempt == max_attempts:
                raise
            delay = base_delay_seconds * attempt
            logger.warning(
                "Database connection unavailable; retrying in %.1fs "
                "(attempt %s/%s)",
                delay,
                attempt + 1,
                max_attempts,
            )
            time.sleep(delay)


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
    conn = connect_database()
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


RUN_RECORD_SOURCES = {
    "apartments": {
        "table": "apartment_staging",
        "name": "building_name",
        "area": "search_area",
        "phone": "contact_phone",
        "website": "contact_website",
        "category": "category",
        "lead_type": "apartment",
    },
    "agencies": {
        "table": "google_places_leads",
        "name": "business_name",
        "area": "area",
        "phone": "phone",
        "website": "website",
        "category": "category",
        "lead_type": "agency",
    },
    "developers": {
        "table": "developer_staging",
        "name": "developer_name",
        "area": "area",
        "phone": "contact_phone",
        "website": "contact_website",
        "category": "membership_tier",
        "lead_type": "developer",
    },
}


def save_run_records(run_id: int, scraper_type: str) -> int:
    """Persist the record-level audit consumed by the scraper UI."""
    source = RUN_RECORD_SOURCES.get(scraper_type)
    if source is None:
        raise RuntimeError(f"Unknown scraper type: {scraper_type}")

    table = source["table"]
    name = source["name"]
    area = source["area"]
    phone = source["phone"]
    website = source["website"]
    category = source["category"]
    lead_type = source["lead_type"]

    scope_filter = (
        "s.run_id = %(run_id)s"
        if scraper_type in {"agencies", "apartments"}
        else "s.scraped_at >= r.started_at"
    )

    conn = psycopg2.connect(get_database_url())
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM scraper_run_records WHERE run_id = %(run_id)s",
            {"run_id": run_id},
        )
        cur.execute(f"""
            INSERT INTO scraper_run_records
                (run_id, name, area, phone, website, category, outcome, reason)
            SELECT
                %(run_id)s,
                s.{name}, s.{area}, s.{phone}, s.{website}, s.{category},
                CASE
                    WHEN EXISTS (
                        SELECT 1 FROM leads l
                        WHERE LOWER(l.name) = LOWER(s.{name})
                          AND l.lead_type = '{lead_type}'
                          AND l.promoted_at >= r.started_at
                    ) THEN 'imported'
                    WHEN EXISTS (
                        SELECT 1 FROM leads l
                        WHERE LOWER(l.name) = LOWER(s.{name})
                          AND l.lead_type = '{lead_type}'
                    ) THEN 'duplicate'
                    ELSE 'rejected'
                END,
                CASE
                    WHEN s.{phone} IS NULL AND s.{website} IS NULL
                        THEN 'no phone or website'
                    WHEN EXISTS (
                        SELECT 1 FROM leads l
                        WHERE LOWER(l.name) = LOWER(s.{name})
                          AND l.lead_type = '{lead_type}'
                          AND l.promoted_at < r.started_at
                    ) THEN 'already exists'
                    ELSE NULL
                END
            FROM {table} s
            JOIN scraper_runs r ON r.id = %(run_id)s
            WHERE {scope_filter}
        """, {"run_id": run_id})
        saved = cur.rowcount
        conn.commit()
        logger.info("Saved %s UI audit records for run %s", saved, run_id)
        return saved
    finally:
        cur.close()
        conn.close()


def summarize_run_records(run_id: int) -> dict:
    conn = psycopg2.connect(get_database_url())
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE phone IS NOT NULL OR website IS NOT NULL),
                COUNT(*) FILTER (WHERE outcome = 'imported'),
                COUNT(*) FILTER (WHERE outcome = 'duplicate'),
                COUNT(*) FILTER (WHERE outcome = 'rejected')
            FROM scraper_run_records
            WHERE run_id = %(run_id)s
        """, {"run_id": run_id})
        found, with_contacts, imported, duplicates, rejected = cur.fetchone()
        return {
            "records_found": found,
            "with_contacts": with_contacts,
            "imported": imported,
            "updated": 0,
            "duplicates": duplicates,
            "rejected": rejected,
        }
    finally:
        cur.close()
        conn.close()


def count_metrics(scraper_type: str, run_id: int | None = None) -> dict:
    conn = psycopg2.connect(get_database_url())
    cur  = conn.cursor()

    table_map = {
        "apartments": "apartment_staging",
        "agencies":   "google_places_leads",
        "developers": "developer_staging",
    }
    table = table_map.get(scraper_type, "google_places_leads")

    try:
        if scraper_type in {"agencies", "apartments"} and run_id is not None:
            cur.execute(f"""
                SELECT COUNT(*)
                FROM {table}
                WHERE run_id = %s
            """, (run_id,))
        else:
            cur.execute(f"""
                SELECT COUNT(*) FROM {table}
                WHERE scraped_at >= NOW() - INTERVAL '30 minutes'
            """)

        records_found = cur.fetchone()[0]

        with_contacts = 0
        if scraper_type == "agencies":
            if run_id is not None:
                cur.execute("""
                    SELECT COUNT(*) FROM google_places_leads
                    WHERE run_id = %s
                      AND NULLIF(TRIM(phone), '') IS NOT NULL
                """, (run_id,))
            else:
                cur.execute("""
                    SELECT COUNT(*)
                    FROM google_places_leads
                    WHERE scraped_at >= NOW() - INTERVAL '30 minutes'
                      AND NULLIF(TRIM(phone), '') IS NOT NULL
                """)

            with_contacts = cur.fetchone()[0]

        elif scraper_type == "apartments":
            if run_id is not None:
                cur.execute("""
                    SELECT COUNT(*) FROM apartment_staging
                    WHERE run_id = %s
                      AND (
                          NULLIF(TRIM(contact_phone), '') IS NOT NULL
                          OR NULLIF(TRIM(contact_email), '') IS NOT NULL
                      )
                """, (run_id,))
            else:
                cur.execute("""
                    SELECT COUNT(*) FROM apartment_staging
                    WHERE scraped_at >= NOW() - INTERVAL '30 minutes'
                      AND (
                          NULLIF(TRIM(contact_phone), '') IS NOT NULL
                          OR NULLIF(TRIM(contact_email), '') IS NOT NULL
                      )
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
    parser.add_argument("--area-id",      type=str)
    parser.add_argument("--areas",        type=str)
    args = parser.parse_args()

    run_id       = args.run_id
    scraper_type = args.scraper_type
    area_id      = args.area_id
    if not area_id and args.areas:
        area_id = args.areas.split(",", 1)[0].strip()

    logger.info(
        "Starting run %s: %s for %s",
        run_id,
        scraper_type,
        area_id or "nationwide",
    )

    start = time.time()

    full_cmd = build_scraper_command(scraper_type, area_id, run_id=run_id)
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
            env=os.environ.copy()
        )

        log_subprocess_output(result, "Scraper")
        ensure_command_succeeded(result, "Scraper")

        scraped_metrics = count_metrics(scraper_type, run_id=run_id)
        ensure_scrape_produced_records(scraped_metrics)

        logger.info("Scrape done — promoting to leads...")

        promotion_cmd = [
            "python",
            "pipeline.py",
            "--skip-scrape",
            "--scraper-type",
            scraper_type,
            "--run-id",
            str(run_id),
        ]

        promotion = subprocess.run(
            promotion_cmd,

            capture_output=True, text=True, timeout=120,
            env=os.environ.copy()
        )
        log_subprocess_output(promotion, "Promotion")
        ensure_command_succeeded(promotion, "Promotion")

        save_run_records(run_id, scraper_type)
        duration = time.time() - start
        metrics = summarize_run_records(run_id)
        ensure_scrape_produced_records(metrics)
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
