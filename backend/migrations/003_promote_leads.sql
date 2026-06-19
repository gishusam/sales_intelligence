
-- ================================================================
-- 003_promote_leads.sql
-- SAFE promotion — never drops leads table.
-- Inserts new leads, updates contact info on existing ones.
-- Never touches status, notes, assigned_to — preserves sales work.
--
-- Run after every scrape session:
--   docker exec -i nzetu_db psql -U nzetu -d nzetu_db < backend/migrations/003_promote_leads.sql
-- ================================================================


-- ── TEMP TRACKING TABLE ───────────────────────────────────────────
-- Captures what happened during this run for the report at the end.

DROP TABLE IF EXISTS _promotion_log;
CREATE TEMP TABLE _promotion_log (
    source      TEXT,
    action      TEXT,   -- inserted / updated / dropped
    reason      TEXT,
    count       INT
);


-- ================================================================
-- SOURCE 1: apartment_staging → lead_type = 'apartment'
-- ================================================================

-- Count dropped (no phone AND no website)
INSERT INTO _promotion_log
SELECT
    'apartment_staging',
    'dropped',
    'no phone or website',
    COUNT(*)
FROM apartment_staging
WHERE building_name IS NOT NULL
  AND TRIM(building_name) != ''
  AND contact_phone IS NULL
  AND contact_website IS NULL;

-- Count duplicates within the batch
INSERT INTO _promotion_log
SELECT
    'apartment_staging',
    'duplicate_in_batch',
    'same normalized name appears multiple times',
    COUNT(*) - COUNT(DISTINCT LOWER(TRIM(REGEXP_REPLACE(building_name, '[^\w\s]', '', 'g'))))
FROM apartment_staging
WHERE building_name IS NOT NULL
  AND (contact_phone IS NOT NULL OR contact_website IS NOT NULL);

-- Do the upsert
WITH source AS (
    SELECT DISTINCT ON (
        LOWER(TRIM(REGEXP_REPLACE(building_name, '[^\w\s]', '', 'g')))
    )
        building_name                                               AS name,
        management_company                                          AS owner_name,
        contact_phone                                               AS phone,
        contact_email                                               AS email,
        contact_website                                             AS website,
        search_area                                                 AS area,
        'apartment'                                                 AS lead_type,
        'google_maps'                                               AS source,
        COALESCE(lead_score, 0.0)                                   AS score,
        LOWER(TRIM(REGEXP_REPLACE(building_name, '[^\w\s]', '', 'g'))) AS name_normalized
    FROM apartment_staging
    WHERE building_name IS NOT NULL
      AND TRIM(building_name) != ''
      AND (contact_phone IS NOT NULL OR contact_website IS NOT NULL)
    ORDER BY
        LOWER(TRIM(REGEXP_REPLACE(building_name, '[^\w\s]', '', 'g'))),
        contact_phone DESC NULLS LAST
),
upserted AS (
    INSERT INTO leads (
        name, owner_name, phone, email, website,
        area, lead_type, source, score, status, name_normalized
    )
    SELECT
        name, owner_name, phone, email, website,
        area, lead_type, source, score, 'new', name_normalized
    FROM source
    ON CONFLICT (name_normalized) DO UPDATE SET
        -- Never touch: status, notes, assigned_to, last_contacted
        -- Only update contact info if we now have better data
        phone      = COALESCE(EXCLUDED.phone,      leads.phone),
        email      = COALESCE(EXCLUDED.email,      leads.email),
        website    = COALESCE(EXCLUDED.website,    leads.website),
        owner_name = COALESCE(EXCLUDED.owner_name, leads.owner_name),
        score      = GREATEST(EXCLUDED.score,      leads.score),
        updated_at = NOW()
    RETURNING id, (xmax = 0) AS is_insert
)
INSERT INTO _promotion_log
SELECT 'apartment_staging', action, 'from apartment_staging', COUNT(*)
FROM (
    SELECT CASE WHEN is_insert THEN 'inserted' ELSE 'updated' END AS action
    FROM upserted
) t
GROUP BY action;


-- ================================================================
-- SOURCE 2: google_places_leads → lead_type = 'agency'
-- ================================================================

INSERT INTO _promotion_log
SELECT
    'google_places_leads',
    'dropped',
    'no phone or website',
    COUNT(*)
FROM google_places_leads
WHERE business_name IS NOT NULL
  AND phone IS NULL
  AND website IS NULL;

INSERT INTO _promotion_log
SELECT
    'google_places_leads',
    'duplicate_in_batch',
    'same normalized name appears multiple times',
    COUNT(*) - COUNT(DISTINCT LOWER(TRIM(REGEXP_REPLACE(business_name, '[^\w\s]', '', 'g'))))
FROM google_places_leads
WHERE business_name IS NOT NULL
  AND (phone IS NOT NULL OR website IS NOT NULL);

WITH source AS (
    SELECT DISTINCT ON (
        LOWER(TRIM(REGEXP_REPLACE(business_name, '[^\w\s]', '', 'g')))
    )
        business_name                                                   AS name,
        phone,
        website,
        area,
        'agency'                                                        AS lead_type,
        'google_places'                                                 AS source,
        LEAST(
            COALESCE(review_count, 0) * 0.15 + COALESCE(rating, 0) * 5,
            100
        )                                                               AS score,
        LOWER(TRIM(REGEXP_REPLACE(business_name, '[^\w\s]', '', 'g'))) AS name_normalized
    FROM google_places_leads
    WHERE business_name IS NOT NULL
      AND TRIM(business_name) != ''
      AND business_name NOT ILIKE '%sponsored%'
      AND (phone IS NOT NULL OR website IS NOT NULL)
    ORDER BY
        LOWER(TRIM(REGEXP_REPLACE(business_name, '[^\w\s]', '', 'g'))),
        phone DESC NULLS LAST
),
upserted AS (
    INSERT INTO leads (
        name, phone, website, area,
        lead_type, source, score, status, name_normalized
    )
    SELECT
        name, phone, website, area,
        lead_type, source, score, 'new', name_normalized
    FROM source
    ON CONFLICT (name_normalized) DO UPDATE SET
        phone      = COALESCE(EXCLUDED.phone,    leads.phone),
        website    = COALESCE(EXCLUDED.website,  leads.website),
        score      = GREATEST(EXCLUDED.score,    leads.score),
        updated_at = NOW()
    RETURNING id, (xmax = 0) AS is_insert
)
INSERT INTO _promotion_log
SELECT 'google_places_leads', action, 'from google_places_leads', COUNT(*)
FROM (
    SELECT CASE WHEN is_insert THEN 'inserted' ELSE 'updated' END AS action
    FROM upserted
) t
GROUP BY action;


-- ================================================================
-- SOURCE 3: listing_staging → 'agency' or 'landlord'
-- ================================================================

INSERT INTO _promotion_log
SELECT
    'listing_staging',
    'dropped',
    'no phone or website',
    COUNT(*)
FROM listing_staging
WHERE COALESCE(owner_name, property_name) IS NOT NULL
  AND owner_phone IS NULL
  AND owner_website IS NULL;

INSERT INTO _promotion_log
SELECT
    'listing_staging',
    'duplicate_in_batch',
    'same normalized name appears multiple times',
    COUNT(*) - COUNT(DISTINCT
        LOWER(TRIM(REGEXP_REPLACE(
            COALESCE(owner_name, property_name, ''),
            '[^\w\s]', '', 'g'
        )))
    )
FROM listing_staging
WHERE COALESCE(owner_name, property_name) IS NOT NULL
  AND (owner_phone IS NOT NULL OR owner_website IS NOT NULL);

WITH source AS (
    SELECT DISTINCT ON (name_normalized)
        name, owner_name, phone, email, website,
        area, lead_type, source, score, name_normalized
    FROM (
        SELECT
            COALESCE(owner_name, property_name)                         AS name,
            owner_name,
            owner_phone                                                  AS phone,
            owner_email                                                  AS email,
            owner_website                                                AS website,
            area,
            CASE
                WHEN owner_type IN ('agency', 'pm') THEN 'agency'
                ELSE 'landlord'
            END                                                          AS lead_type,
            source,
            LEAST(
                COUNT(*) OVER (
                    PARTITION BY LOWER(TRIM(COALESCE(owner_name, '')))
                ) * 8.0
                + CASE WHEN owner_phone IS NOT NULL THEN 20 ELSE 0 END
                + CASE WHEN owner_email IS NOT NULL THEN 10 ELSE 0 END,
                100
            )                                                            AS score,
            LOWER(TRIM(REGEXP_REPLACE(
                COALESCE(owner_name, property_name, ''),
                '[^\w\s]', '', 'g'
            )))                                                          AS name_normalized
        FROM listing_staging
        WHERE COALESCE(owner_name, property_name) IS NOT NULL
          AND TRIM(COALESCE(owner_name, property_name, '')) != ''
          AND (owner_phone IS NOT NULL OR owner_website IS NOT NULL)
    ) sub
    ORDER BY name_normalized, phone DESC NULLS LAST
),
upserted AS (
    INSERT INTO leads (
        name, owner_name, phone, email, website,
        area, lead_type, source, score, status, name_normalized
    )
    SELECT
        name, owner_name, phone, email, website,
        area, lead_type, source, score, 'new', name_normalized
    FROM source
    ON CONFLICT (name_normalized) DO UPDATE SET
        phone      = COALESCE(EXCLUDED.phone,      leads.phone),
        email      = COALESCE(EXCLUDED.email,      leads.email),
        website    = COALESCE(EXCLUDED.website,    leads.website),
        owner_name = COALESCE(EXCLUDED.owner_name, leads.owner_name),
        score      = GREATEST(EXCLUDED.score,      leads.score),
        updated_at = NOW()
    RETURNING id, (xmax = 0) AS is_insert
)
INSERT INTO _promotion_log
SELECT 'listing_staging', action, 'from listing_staging', COUNT(*)
FROM (
    SELECT CASE WHEN is_insert THEN 'inserted' ELSE 'updated' END AS action
    FROM upserted
) t
GROUP BY action;


-- ================================================================
-- REPORT — what happened this run
-- ================================================================

SELECT
    '─────────────────────────────────────────' AS "PROMOTION REPORT";

SELECT
    source                          AS "Source",
    action                          AS "Action",
    reason                          AS "Reason",
    count                           AS "Count"
FROM _promotion_log
ORDER BY source, action;



SELECT
    SUM(count) FILTER (WHERE action = 'inserted')         AS "New leads inserted",
    SUM(count) FILTER (WHERE action = 'updated')          AS "Existing leads updated",
    SUM(count) FILTER (WHERE action = 'dropped')          AS "Dropped (no contact)",
    SUM(count) FILTER (WHERE action = 'duplicate_in_batch') AS "Duplicates skipped"
FROM _promotion_log;



-- Current leads table state
SELECT
    lead_type                       AS "Type",
    COUNT(*)                        AS "Total",
    COUNT(phone)                    AS "Has Phone",
    COUNT(website)                  AS "Has Website",
    COUNT(*) FILTER (WHERE status = 'new')          AS "New",
    COUNT(*) FILTER (WHERE status != 'new')         AS "In Pipeline",
    ROUND(AVG(score)::numeric, 1)   AS "Avg Score"
FROM leads
GROUP BY lead_type
ORDER BY "Total" DESC;

DROP TABLE IF EXISTS _promotion_log;
