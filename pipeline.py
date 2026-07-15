"""
Nyumba Zetu Sales Intelligence Pipeline
========================================
One command runs the full pipeline:
  POSTGRES_HOST=localhost python pipeline.py

What it runs in order:
  Step 1  — BuyRentKenya listings scrape
  Step 2a — Google Maps agency scrape
  Step 2b — Google Maps apartment discovery + enrichment
  Step 3  — Clean noise from all raw tables
  Step 4  — Score listing_staging leads
  Step 5  — Promote to production leads table
  Step 6  — Summary report

Options:
  --skip-scrape   Skip steps 1, 2a, 2b (use existing data)
  --areas         Comma-separated areas for Google Maps
"""

import os
import subprocess
import logging
import psycopg2
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [pipeline] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

DB = dict(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=os.getenv("POSTGRES_PORT", "5432"),
    dbname=os.getenv("POSTGRES_DB", "nzetu_db"),
    user=os.getenv("POSTGRES_USER", "nzetu"),
    password=os.getenv("POSTGRES_PASSWORD", "changeme"),
)

DEFAULT_AREAS = [
    "Kilimani", "Muthaiga", "Westlands", "Lavington",
    "Parklands", "Lenana Road", "Kindaruma Road",
    "Mugunga Road", "South C", "South B", "Kileleshwa",
    "Karen", "Runda", "Spring Valley", "Ruaka",
]


def step(msg):
    logger.info(f"\n{'─'*55}\n{msg}\n{'─'*55}")


def run_subprocess(cmd, env_extra=None):
    env = {**os.environ, "POSTGRES_HOST": DB["host"]}
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        logger.warning(
            f"Command exited with code {result.returncode}: {' '.join(cmd)}"
        )


def run(areas=None, skip_scrape=False):
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    areas = areas or DEFAULT_AREAS

    # ── STEP 1: Scrape BuyRentKenya ───────────────────────────────
    if not skip_scrape:
        step("STEP 1 — Scraping BuyRentKenya listings")
        run_subprocess(
            ["python", "-m", "scrapy", "crawl", "buyrentkenya", "-L", "INFO"],
            {"REDIS_URL": "redis://localhost:6379/0"}
        )
    else:
        logger.info("STEP 1 — Skipped (--skip-scrape)")

    # ── STEP 2a: Google Maps agencies ─────────────────────────────
    if not skip_scrape:
        step("STEP 2a — Scraping Google Maps (property managers)")
        run_subprocess([
            "python", "scraper/spiders/googlemaps.py",
            "--areas", ",".join(areas)
        ])
    else:
        logger.info("STEP 2a — Skipped (--skip-scrape)")

    # ── STEP 2b: Apartments discovery + enrichment ────────────────
    if not skip_scrape:
        step("STEP 2b — Apartment discovery + enrichment")
        run_subprocess([
            "python", "scraper/spiders/apartments.py",
            "--areas", ",".join(areas)
        ])
    else:
        logger.info("STEP 2b — Skipped (--skip-scrape)")

    # ── STEP 3: Clean noise ───────────────────────────────────────
    step("STEP 3 — Cleaning raw tables")

    cur.execute("""
        DELETE FROM google_places_leads
        WHERE business_name = 'Sponsored'
           OR business_name ILIKE '%Nyumba Zetu%'
           OR business_name ILIKE '%BuyRentKenya%'
           OR business_name ILIKE '%Bomahut%'
    """)
    logger.info(f"Removed {cur.rowcount} noise rows from google_places_leads")

    cur.execute("""
        DELETE FROM apartment_staging
        WHERE building_name ILIKE '%Nyumba Zetu%'
           OR building_name ILIKE '%Bomahut%'
           OR building_name = 'Sponsored'
    """)
    logger.info(f"Removed {cur.rowcount} noise rows from apartment_staging")

    cur.execute("""
        UPDATE listing_staging
        SET owner_phone = REGEXP_REPLACE(owner_phone, '[^0-9+]', '', 'g')
        WHERE owner_phone IS NOT NULL
    """)
    conn.commit()

    # ── STEP 4: Score listing_staging ────────────────────────────
    step("STEP 4 — Scoring listing_staging leads")

    cur.execute("""
        SELECT area, COUNT(*) FROM listing_staging
        WHERE area IS NOT NULL GROUP BY area
    """)
    zone_counts = {r[0]: r[1] for r in cur.fetchall()}
    max_zone = max(zone_counts.values()) if zone_counts else 1
    owner_weights = {"agency": 100, "pm": 80, "owner": 60}

    cur.execute("""
        SELECT id, bedrooms, owner_type, area, listing_age_days
        FROM listing_staging
    """)
    rows = cur.fetchall()

    for row_id, bedrooms, otype, area, age_days in rows:
        unit_score    = min((bedrooms or 1) / 6 * 40, 40)
        type_score    = owner_weights.get(otype, 60) * 35 / 100
        density_score = (zone_counts.get(area, 1) / max_zone) * 25

        total = round(unit_score + type_score + density_score, 1)

        # Recency adjustment — CEO request
        if age_days is not None and age_days > 365:
            total = round(total * 0.7, 1)

        priority = "HOT" if total >= 65 else "WARM" if total >= 45 else "COLD"
        cur.execute(
            "UPDATE listing_staging SET score=%s, priority=%s WHERE id=%s",
            (total, priority, row_id)
        )

    conn.commit()
    logger.info(f"Scored {len(rows)} listing_staging records")

    # ── STEP 5: Promote to production leads table ─────────────────
    step("STEP 5 — Promoting to production leads table")

    # 5a: From listing_staging (BuyRentKenya + Jiji)
    cur.execute("""
        INSERT INTO leads (
            name, owner_name, owner_type, owner_url,
            raw_location, raw_price, phone, email,
            unit_count, score, status,
            google_rating, google_reviews, lead_quality,
            lead_type, source, source_url, promoted_at
        )
        SELECT DISTINCT ON (s.source_url)
            s.property_name,
            s.owner_name,
            s.owner_type,
            s.owner_website,
            s.area,
            s.raw_price,
            COALESCE(s.owner_phone, g.phone),
            s.owner_email,
            s.bedrooms,
            s.score,
            'new',
            g.rating,
            g.review_count,
            CASE
                WHEN s.score >= 65 AND g.rating IS NOT NULL
                    THEN 'VERIFIED + ACTIVE'
                WHEN s.score >= 65
                    THEN 'ACTIVE LISTER'
                WHEN g.rating IS NOT NULL
                    THEN 'VERIFIED BUSINESS'
                ELSE 'NEEDS RESEARCH'
            END,
            CASE
                WHEN s.owner_type IN ('agency', 'pm') THEN 'agency'
                ELSE 'landlord'
            END,
            s.source,
            s.source_url,
            NOW()
        FROM listing_staging s
        LEFT JOIN google_places_leads g
            ON LOWER(s.owner_name) LIKE
               '%' || LOWER(SPLIT_PART(g.business_name, ' ', 1)) || '%'
           AND LOWER(s.area) = LOWER(g.area)
        WHERE s.owner_name IS NOT NULL
          AND s.promoted = FALSE
        ON CONFLICT DO NOTHING
    """)
    from_listings = cur.rowcount
    logger.info(f"Promoted {from_listings} leads from listing_staging")

    # 5b: Google Maps agency leads
    cur.execute("""
        INSERT INTO leads (
            name, owner_name, owner_type,
            area, phone, website,
            lead_quality, lead_type, source, status, score, promoted_at
        )
        SELECT DISTINCT ON (g.business_name, g.area)
            g.business_name, g.business_name, 'agency',
            g.area, g.phone, g.website,
            CASE
                WHEN g.rating >= 4.0 THEN 'VERIFIED BUSINESS'
                ELSE 'MAPS ONLY'
            END,
            'agency',
            'google_maps', 'new',
            20,
            NOW()
        FROM google_places_leads g
        WHERE NOT EXISTS (
            SELECT 1 FROM leads l
            WHERE LOWER(l.owner_name) LIKE
                  '%' || LOWER(SPLIT_PART(g.business_name, ' ', 1)) || '%'
              AND LOWER(l.area) = LOWER(g.area)
        )
        ON CONFLICT DO NOTHING
    """)
    from_google = cur.rowcount
    logger.info(f"Promoted {from_google} Google Maps agency leads")

    # 5c: Apartment staging leads
    cur.execute("""
        INSERT INTO leads (
            name, owner_name, owner_type,
            area, phone, email, website,
            lead_quality, lead_type, source, status, score, promoted_at
        )
        SELECT DISTINCT ON (a.id)
            a.building_name,
            COALESCE(a.management_company, a.building_name),
            'agency',
            a.search_area,
            a.contact_phone,
            a.contact_email,
            a.contact_website,
            CASE
                WHEN a.confidence = 'high'   THEN 'VERIFIED + ACTIVE'
                WHEN a.confidence = 'medium' THEN 'VERIFIED BUSINESS'
                ELSE 'APARTMENT LEAD'
            END,
            'apartment',
            'apartment_discovery',
            'new',
            a.lead_score,
            NOW()
        FROM apartment_staging a
        WHERE a.lead_score >= 40
          AND NOT EXISTS (
              SELECT 1 FROM leads l
              WHERE LOWER(l.name) LIKE
                    '%' || LOWER(SPLIT_PART(a.building_name, ' ', 1)) || '%'
                AND LOWER(l.area) = LOWER(a.search_area)
          )
        ON CONFLICT DO NOTHING
    """)
    from_apts = cur.rowcount
    logger.info(f"Promoted {from_apts} apartment leads")

    # 5d: Developer leads from KPDA directory
    cur.execute("""
        INSERT INTO leads (
            name, owner_name, owner_type,
            area, phone, email, website,
            lead_quality, lead_type, source,
            status, score, promoted_at
        )
        SELECT DISTINCT ON (d.developer_name)
            d.developer_name,
            d.developer_name,
            'developer',
            d.area,
            d.contact_phone,
            d.contact_email,
            d.contact_website,
            CASE
                WHEN d.membership_tier = 'PLATINUM'       THEN 'VERIFIED BUSINESS'
                WHEN d.membership_tier = 'ASSOCIATE GOLD' THEN 'VERIFIED BUSINESS'
                ELSE 'MAPS ONLY'
            END,
            'developer',
            'kpda_directory',
            'new',
            d.tier_score,
            NOW()
        FROM developer_staging d
        WHERE d.developer_name IS NOT NULL
          AND (d.contact_phone IS NOT NULL OR d.contact_website IS NOT NULL)
          AND NOT EXISTS (
              SELECT 1 FROM leads l
              WHERE LOWER(l.name) = LOWER(d.developer_name)
          )
        ON CONFLICT DO NOTHING
    """)
    from_devs = cur.rowcount
    logger.info(f"Promoted {from_devs} developer leads")

    # Mark listing_staging as promoted
    cur.execute("""
        UPDATE listing_staging SET promoted = TRUE
        WHERE promoted = FALSE AND owner_name IS NOT NULL
    """)
    conn.commit()

    # ── STEP 6: Summary ───────────────────────────────────────────
    step("STEP 6 — Summary")

    cur.execute("SELECT COUNT(*) FROM leads")
    total_leads = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM listing_staging")
    total_staging = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM google_places_leads")
    total_google = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM apartment_staging")
    total_apts = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM apartment_staging
        WHERE enrichment_status = 'done'
    """)
    total_enriched = cur.fetchone()[0]

    cur.execute("""
        SELECT lead_quality, COUNT(*)
        FROM leads GROUP BY lead_quality ORDER BY COUNT(*) DESC
    """)
    by_quality = cur.fetchall()

    cur.execute("""
        SELECT area, COUNT(*)
        FROM leads WHERE area IS NOT NULL
        GROUP BY area ORDER BY COUNT(*) DESC LIMIT 10
    """)
    by_area = cur.fetchall()

    cur.execute("""
        SELECT COALESCE(owner_name, name), raw_location, phone, score, lead_quality
        FROM leads
        WHERE phone IS NOT NULL
        ORDER BY score DESC NULLS LAST
        LIMIT 20
    """)
    top_leads = cur.fetchall()

    cur.close()
    conn.close()

    print(f"\n{'='*65}")
    print(f"PIPELINE COMPLETE — {datetime.now().strftime('%d %b %Y %H:%M')}")
    print(f"{'='*65}")
    print(f"\nData sources:")
    print(f"  listing_staging (BuyRentKenya+Jiji): {total_staging:>6}")
    print(f"  google_places_leads (agencies):      {total_google:>6}")
    print(f"  apartment_staging (buildings):       {total_apts:>6} "
          f"({total_enriched} enriched)")
    print(f"\nProduction leads: {total_leads} total")
    print(f"  This run: {from_listings} listings + "
          f"{from_google} agencies + {from_apts} apartments + "
          f"{from_devs} developers")

    print(f"\nBy quality tier:")
    for quality, count in by_quality:
        bar = "█" * min(count // 3, 25)
        print(f"  {(quality or 'unknown'):<25} {count:>4}  {bar}")

    print(f"\nTop areas:")
    for area, count in by_area:
        print(f"  {area:<25} {count} leads")

    print(f"\nTop 20 leads to call:")
    print(f"  {'Company':<38} {'Area':<16} {'Score':>5}  {'Quality'}")
    print(f"  {'─'*78}")
    for company, location, phone, score, quality in top_leads:
        cs = (company[:35]+"..") if company and len(company)>35 else (company or "—")
        print(f"  {cs:<38} {(location or '—'):<16} "
              f"{score or 0:>5}  {quality or '—'}")
    print(f"\n{'='*65}\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Nyumba Zetu pipeline")
    parser.add_argument("--skip-scrape", action="store_true",
                        help="Skip scraping, reprocess existing data")
    parser.add_argument("--areas", type=str,
                        help="Comma-separated areas")
    args = parser.parse_args()

    areas = [a.strip() for a in args.areas.split(",")] if args.areas else None
    run(areas=areas, skip_scrape=args.skip_scrape)
