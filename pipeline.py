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


def run(areas=None, skip_scrape=False, run_id=None, scraper_type=None):
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        conn = psycopg2.connect(database_url)
    else:
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

    if scraper_type in (None, "agencies"):
        if run_id is not None:
            cur.execute("""
                DELETE FROM google_places_leads
                WHERE run_id = %s
                AND (
                    business_name = 'Sponsored'
                    OR business_name ILIKE '%%Nyumba Zetu%%'
                    OR business_name ILIKE '%%BuyRentKenya%%'
                    OR business_name ILIKE '%%Bomahut%%'
                )
            """, (run_id,))
        else:
            cur.execute("""
                DELETE FROM google_places_leads
                WHERE business_name = 'Sponsored'
                OR business_name ILIKE '%%Nyumba Zetu%%'
                OR business_name ILIKE '%%BuyRentKenya%%'
                OR business_name ILIKE '%%Bomahut%%'
            """)

        logger.info(
            f"Removed {cur.rowcount} noise rows from google_places_leads"
        )
    logger.info(f"Removed {cur.rowcount} noise rows from google_places_leads")

    if scraper_type in (None, "apartments"):
        cur.execute("""
            DELETE FROM apartment_staging
            WHERE building_name ILIKE '%%Nyumba Zetu%%'
            OR building_name ILIKE '%%Bomahut%%'
            OR building_name = 'Sponsored'
        """)
        logger.info(
            f"Removed {cur.rowcount} noise rows from apartment_staging"
        )

    if scraper_type is None:
        cur.execute("""
            UPDATE listing_staging
            SET owner_phone = REGEXP_REPLACE(
                owner_phone,
                '[^0-9+]',
                '',
                'g'
            )
            WHERE owner_phone IS NOT NULL
        """)
    conn.commit()

    # ── STEP 4: Score listing_staging ────────────────────────────
    if scraper_type is None:
        step("STEP 4 — Scoring listing_staging leads")

        cur.execute("""
            SELECT area, COUNT(*)
            FROM listing_staging
            WHERE area IS NOT NULL
            GROUP BY area
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
            unit_score = min((bedrooms or 1) / 6 * 40, 40)
            type_score = owner_weights.get(otype, 60) * 35 / 100
            density_score = (
                zone_counts.get(area, 1) / max_zone
            ) * 25

            total = round(
                unit_score + type_score + density_score,
                1,
            )

            # Recency adjustment — CEO request
            if age_days is not None and age_days > 365:
                total = round(total * 0.7, 1)

            priority = (
                "HOT"
                if total >= 65
                else "WARM"
                if total >= 45
                else "COLD"
            )

            cur.execute(
                """
                UPDATE listing_staging
                SET score = %s,
                    priority = %s
                WHERE id = %s
                """,
                (total, priority, row_id),
            )

        conn.commit()
        logger.info(
            f"Scored {len(rows)} listing_staging records"
        )
    else:
        logger.info(
            "STEP 4 — Skipped listing scoring "
            f"for {scraper_type} run"
        )


    # ── Promotion scope ───────────────────────────────────────────
    promote_listings = scraper_type is None
    promote_agencies = scraper_type in (None, "agencies")
    promote_apartments = scraper_type in (None, "apartments")
    promote_developers = scraper_type in (None, "developers")
    # ── STEP 5: Promote to production leads table ─────────────────
    step("STEP 5 — Promoting to production leads table")

    # 5a: From listing_staging (BuyRentKenya + Jiji)
    cur.execute("""
        INSERT INTO leads (
            name, owner_name, owner_type,
            phone, email, website, area,
            score, status, lead_quality,
            lead_type, source, source_url, promoted_at
        )
        SELECT DISTINCT ON (s.source_url)
            s.property_name,
            s.owner_name,
            s.owner_type,
            COALESCE(s.owner_phone, g.phone),
            s.owner_email,
            COALESCE(s.owner_website, g.website),
            s.area,
            s.score,
            'new',
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
               '%%' || LOWER(SPLIT_PART(g.business_name, ' ', 1)) || '%%'
           AND LOWER(s.area) = LOWER(g.area)
        WHERE %s
            AND s.owner_name IS NOT NULL
            AND s.promoted = FALSE
        ON CONFLICT DO NOTHING
    """, (promote_listings,))
    from_listings = cur.rowcount
    logger.info(f"Promoted {from_listings} leads from listing_staging")
# 5b: Google Maps agency leads
    agency_run_filter = ""
    agency_params = [promote_agencies]

    if run_id is not None:
        agency_run_filter = "AND g.run_id = %s"
        agency_params.append(run_id)

    cur.execute(f"""
        WITH normalized_agencies AS (
            SELECT
                g.id,
                g.business_name,
                g.area,
                g.phone,
                g.website,
                NULLIF(TRIM(g.maps_url), '') AS maps_url,
                g.rating,
                g.review_count,

                LOWER(
                    TRIM(
                        REGEXP_REPLACE(
                            g.business_name,
                            '[^\\w\\s]',
                            '',
                            'g'
                        )
                    )
                ) AS normalized_name,

                CASE
                    WHEN REGEXP_REPLACE(
                        COALESCE(g.phone, ''),
                        '\\D',
                        '',
                        'g'
                    ) ~ '^0[17][0-9]{{8}}$'
                    THEN
                        '254' ||
                        SUBSTRING(
                            REGEXP_REPLACE(
                                g.phone,
                                '\\D',
                                '',
                                'g'
                            )
                            FROM 2
                        )
                    ELSE
                        REGEXP_REPLACE(
                            COALESCE(g.phone, ''),
                            '\\D',
                            '',
                            'g'
                        )
                END AS normalized_phone

            FROM google_places_leads g

            WHERE %s
                {agency_run_filter}
                AND NULLIF(TRIM(g.phone), '') IS NOT NULL
                AND NULLIF(TRIM(g.business_name), '') IS NOT NULL
        ),

        agency_by_contact AS (
            /*
             * Same business name + same phone = same agency,
             * regardless of which area/search produced it.
             */
            SELECT DISTINCT ON (
                normalized_name,
                normalized_phone
            )
                *
            FROM normalized_agencies

            ORDER BY
                normalized_name,
                normalized_phone,
                (maps_url IS NOT NULL) DESC,
                rating DESC NULLS LAST,
                review_count DESC NULLS LAST,
                id
        ),

        agency_candidates AS (
            /*
             * Also collapse records that point to the exact same
             * Google Maps business.
             */
            SELECT DISTINCT ON (
                COALESCE(
                    maps_url,
                    'contact:' || normalized_name || '|' || normalized_phone
                )
            )
                *
            FROM agency_by_contact

            ORDER BY
                COALESCE(
                    maps_url,
                    'contact:' || normalized_name || '|' || normalized_phone
                ),
                rating DESC NULLS LAST,
                review_count DESC NULLS LAST,
                id
        ),

        updated AS (
            UPDATE leads l

            SET
                phone = COALESCE(
                    NULLIF(TRIM(l.phone), ''),
                    c.phone
                ),

                website = COALESCE(
                    NULLIF(TRIM(l.website), ''),
                    c.website
                ),

                source_url = COALESCE(
                    NULLIF(TRIM(l.source_url), ''),
                    c.maps_url
                ),

                updated_at = NOW()

            FROM agency_candidates c

            WHERE l.lead_type = 'agency'

              AND (
                    /* Strongest identity: same Google Maps business */
                    (
                        c.maps_url IS NOT NULL
                        AND l.source_url = c.maps_url
                    )

                    OR

                    /* Fallback: same business name + same phone */
                    (
                        LOWER(
                            TRIM(
                                REGEXP_REPLACE(
                                    l.name,
                                    '[^\\w\\s]',
                                    '',
                                    'g'
                                )
                            )
                        ) = c.normalized_name

                        AND

                        CASE
                            WHEN REGEXP_REPLACE(
                                COALESCE(l.phone, ''),
                                '\\D',
                                '',
                                'g'
                            ) ~ '^0[17][0-9]{{8}}$'
                            THEN
                                '254' ||
                                SUBSTRING(
                                    REGEXP_REPLACE(
                                        l.phone,
                                        '\\D',
                                        '',
                                        'g'
                                    )
                                    FROM 2
                                )
                            ELSE
                                REGEXP_REPLACE(
                                    COALESCE(l.phone, ''),
                                    '\\D',
                                    '',
                                    'g'
                                )
                        END = c.normalized_phone
                    )
              )

            RETURNING l.id
        )

        INSERT INTO leads (
            name,
            owner_name,
            owner_type,
            area,
            phone,
            website,
            lead_quality,
            lead_type,
            source,
            source_url,
            status,
            score,
            promoted_at,
            assigned_to
        )

        SELECT
            c.business_name,
            c.business_name,
            'agency',
            c.area,
            c.phone,
            c.website,

            CASE
                WHEN c.rating >= 4.0 THEN 'VERIFIED BUSINESS'
                ELSE 'MAPS ONLY'
            END,

            'agency',
            'google_maps',
            c.maps_url,
            'new',
            20,
            NOW(),

            CASE
                WHEN %s THEN (
                    SELECT started_by
                    FROM scraper_runs
                    WHERE id = %s
                )
                ELSE NULL
            END

        FROM agency_candidates c

        WHERE NOT EXISTS (
            SELECT 1
            FROM leads l

            WHERE l.lead_type = 'agency'

              AND (
                    (
                        c.maps_url IS NOT NULL
                        AND l.source_url = c.maps_url
                    )

                    OR

                    (
                        LOWER(
                            TRIM(
                                REGEXP_REPLACE(
                                    l.name,
                                    '[^\\w\\s]',
                                    '',
                                    'g'
                                )
                            )
                        ) = c.normalized_name

                        AND

                        CASE
                            WHEN REGEXP_REPLACE(
                                COALESCE(l.phone, ''),
                                '\\D',
                                '',
                                'g'
                            ) ~ '^0[17][0-9]{{8}}$'
                            THEN
                                '254' ||
                                SUBSTRING(
                                    REGEXP_REPLACE(
                                        l.phone,
                                        '\\D',
                                        '',
                                        'g'
                                    )
                                    FROM 2
                                )
                            ELSE
                                REGEXP_REPLACE(
                                    COALESCE(l.phone, ''),
                                    '\\D',
                                    '',
                                    'g'
                                )
                        END = c.normalized_phone
                    )
              )
        )

        ON CONFLICT DO NOTHING
    """, tuple(
        agency_params +
        [run_id is not None, run_id]
    ))

    from_google = cur.rowcount
    logger.info(f"Promoted {from_google} new Google Maps agency leads")


# 5c: Apartment staging leads
    apartment_run_filter = ""
    apartment_params = [promote_apartments]

    if run_id is not None:
        apartment_run_filter = "AND a.run_id = %s"
        apartment_params.append(run_id)

    cur.execute(f"""
        WITH apartment_candidates AS (
            SELECT DISTINCT ON (a.maps_url)
                a.building_name,
                COALESCE(a.management_company, a.building_name) AS owner_name,
                a.search_area,
                a.contact_phone,
                a.contact_email,
                a.contact_website,
                a.maps_url,
                a.lead_score,

                CASE
                    WHEN a.confidence = 'high'
                        THEN 'VERIFIED + ACTIVE'
                    WHEN a.confidence = 'medium'
                        THEN 'VERIFIED BUSINESS'
                    ELSE 'APARTMENT LEAD'
                END AS lead_quality,

                LOWER(
                    TRIM(
                        REGEXP_REPLACE(
                            a.building_name,
                            '[^\\w\\s]',
                            '',
                            'g'
                        )
                    )
                ) AS normalized_name,

                CASE
                    WHEN REGEXP_REPLACE(
                        COALESCE(a.contact_phone, ''),
                        '\\D',
                        '',
                        'g'
                    ) ~ '^0[17][0-9]{8}$'
                    THEN
                        '254' ||
                        SUBSTRING(
                            REGEXP_REPLACE(
                                a.contact_phone,
                                '\\D',
                                '',
                                'g'
                            )
                            FROM 2
                        )
                    ELSE
                        REGEXP_REPLACE(
                            COALESCE(a.contact_phone, ''),
                            '\\D',
                            '',
                            'g'
                        )
                END AS normalized_phone,

                LOWER(
                    NULLIF(
                        TRIM(a.contact_email),
                        ''
                    )
                ) AS normalized_email

            FROM apartment_staging a

            WHERE %s
            {apartment_run_filter}
            AND a.lead_score >= 50
            AND a.maps_url IS NOT NULL
            AND a.building_name IS NOT NULL
            AND TRIM(a.building_name) <> ''
            AND (
                NULLIF(TRIM(a.contact_phone), '') IS NOT NULL
                OR NULLIF(TRIM(a.contact_email), '') IS NOT NULL
            )

            ORDER BY
                a.maps_url,
                a.lead_score DESC,
                a.id
        ),

        updated AS (
            UPDATE leads l

            SET
                phone = COALESCE(l.phone, c.contact_phone),
                email = COALESCE(l.email, c.contact_email),
                website = COALESCE(l.website, c.contact_website),
                owner_name = COALESCE(l.owner_name, c.owner_name),

                source_url = COALESCE(
                    l.source_url,
                    c.maps_url
                ),

                score = GREATEST(
                    COALESCE(l.score, 0),
                    COALESCE(c.lead_score, 0)
                ),

                updated_at = NOW()

            FROM apartment_candidates c

            WHERE l.lead_type = 'apartment'

            AND (
                /* Strongest identity: same Google Maps place */
                l.source_url = c.maps_url

                OR

                /* Fallback for older leads that do not yet have source_url */
                (
                    LOWER(
                        TRIM(
                            REGEXP_REPLACE(
                                l.name,
                                '[^\\w\\s]',
                                '',
                                'g'
                            )
                        )
                    ) = c.normalized_name

                    AND (
                        (
                            c.normalized_phone <> ''

                            AND

                            CASE
                                WHEN REGEXP_REPLACE(
                                    COALESCE(l.phone, ''),
                                    '\\D',
                                    '',
                                    'g'
                                ) ~ '^0[17][0-9]{8}$'
                                THEN
                                    '254' ||
                                    SUBSTRING(
                                        REGEXP_REPLACE(
                                            l.phone,
                                            '\\D',
                                            '',
                                            'g'
                                        )
                                        FROM 2
                                    )

                                ELSE
                                    REGEXP_REPLACE(
                                        COALESCE(l.phone, ''),
                                        '\\D',
                                        '',
                                        'g'
                                    )
                            END = c.normalized_phone
                        )

                        OR

                        (
                            c.normalized_email IS NOT NULL
                            AND LOWER(TRIM(l.email))
                                = c.normalized_email
                        )
                    )
                )
            )

            RETURNING l.id
        )

        INSERT INTO leads (
            name,
            owner_name,
            owner_type,
            area,
            phone,
            email,
            website,
            lead_quality,
            lead_type,
            source,
            source_url,
            status,
            score,
            promoted_at,
            assigned_to
        )

        SELECT
            c.building_name,
            c.owner_name,
            'agency',
            c.search_area,
            c.contact_phone,
            c.contact_email,
            c.contact_website,
            c.lead_quality,
            'apartment',
            'apartment_discovery',
            c.maps_url,
            'new',
            c.lead_score,
            NOW(),
            (
                SELECT started_by
                FROM scraper_runs
                WHERE id = %s
            )

        FROM apartment_candidates c

        WHERE NOT EXISTS (
            SELECT 1
            FROM leads l

            WHERE l.lead_type = 'apartment'

            AND (
                l.source_url = c.maps_url

                OR

                (
                    LOWER(
                        TRIM(
                            REGEXP_REPLACE(
                                l.name,
                                '[^\\w\\s]',
                                '',
                                'g'
                            )
                        )
                    ) = c.normalized_name

                    AND (
                        (
                            c.normalized_phone <> ''

                            AND

                            CASE
                                WHEN REGEXP_REPLACE(
                                    COALESCE(l.phone, ''),
                                    '\\D',
                                    '',
                                    'g'
                                ) ~ '^0[17][0-9]{8}$'
                                THEN
                                    '254' ||
                                    SUBSTRING(
                                        REGEXP_REPLACE(
                                            l.phone,
                                            '\\D',
                                            '',
                                            'g'
                                        )
                                        FROM 2
                                    )

                                ELSE
                                    REGEXP_REPLACE(
                                        COALESCE(l.phone, ''),
                                        '\\D',
                                        '',
                                        'g'
                                    )
                            END = c.normalized_phone
                        )

                        OR

                        (
                            c.normalized_email IS NOT NULL
                            AND LOWER(TRIM(l.email))
                                = c.normalized_email
                        )
                    )
                )
            )
        )

        ON CONFLICT DO NOTHING
    """, tuple(apartment_params + [run_id]))

    from_apts = cur.rowcount
    logger.info(f"Promoted {from_apts} new apartment leads")

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
        WHERE %s
          AND d.developer_name IS NOT NULL
          AND (d.contact_phone IS NOT NULL OR d.contact_website IS NOT NULL)
          AND NOT EXISTS (
              SELECT 1 FROM leads l
              WHERE LOWER(l.name) = LOWER(d.developer_name)
          )
        ON CONFLICT DO NOTHING
    """, (promote_developers,))
    from_devs = cur.rowcount
    logger.info(f"Promoted {from_devs} developer leads")

    # Mark listing_staging as promoted
    # Mark listing staging only during the full pipeline.
    if scraper_type is None:
        cur.execute("""
            UPDATE listing_staging
            SET promoted = TRUE
            WHERE promoted = FALSE
            AND owner_name IS NOT NULL
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
        SELECT COALESCE(owner_name, name), area, phone, score, lead_quality
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
    parser.add_argument(
        "--run-id",
        type=int,
        help="Only promote staging records belonging to this scraper run",
    )
    parser.add_argument(
    "--scraper-type",
    choices=["agencies", "apartments", "developers"],
    )

    args = parser.parse_args()

    areas = [a.strip() for a in args.areas.split(",")] if args.areas else None
    run(areas=areas, skip_scrape=args.skip_scrape, run_id=args.run_id, scraper_type=args.scraper_type)
