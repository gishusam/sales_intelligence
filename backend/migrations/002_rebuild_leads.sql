-- ================================================================
-- 002_rebuild_leads.sql
-- Clean rebuild with:
--   1. Only leads with phone OR website (Option B)
--   2. Dedup fixed for listing_staging
--   3. Landlords included
--
-- Run:
--   docker exec -i nzetu_db psql -U nzetu -d nzetu_db < backend/migrations/002_rebuild_leads.sql
-- ================================================================

-- Drop and recreate clean
DROP TABLE IF EXISTS leads CASCADE;

CREATE TABLE leads (
    id               SERIAL PRIMARY KEY,
    name             TEXT NOT NULL,
    owner_name       TEXT,
    phone            TEXT,
    email            TEXT,
    website          TEXT,
    area             TEXT,
    lead_type        TEXT NOT NULL,
    source           TEXT NOT NULL,
    score            FLOAT   DEFAULT 0.0,
    status           TEXT    DEFAULT 'new',
    notes            TEXT,
    assigned_to      TEXT,
    last_contacted   TIMESTAMPTZ,
    -- Dedup key: normalized name only (not name+area) to prevent
    -- same company appearing multiple times across different areas
    name_normalized  TEXT UNIQUE NOT NULL,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_leads_lead_type ON leads(lead_type);
CREATE INDEX idx_leads_status    ON leads(status);
CREATE INDEX idx_leads_area      ON leads(area);
CREATE INDEX idx_leads_score     ON leads(score DESC);
CREATE INDEX idx_leads_assigned  ON leads(assigned_to);


-- ── SOURCE 1: apartment_staging → apartments ─────────────────────
-- Only where phone OR website exists (Option B)
INSERT INTO leads (
    name, owner_name, phone, email, website,
    area, lead_type, source, score, status, name_normalized
)
SELECT DISTINCT ON (
    LOWER(TRIM(REGEXP_REPLACE(building_name, '[^\w\s]', '', 'g')))
)
    building_name,
    management_company,
    contact_phone,
    contact_email,
    contact_website,
    search_area,
    'apartment',
    'google_maps',
    COALESCE(lead_score, 0.0),
    'new',
    LOWER(TRIM(REGEXP_REPLACE(building_name, '[^\w\s]', '', 'g')))
FROM apartment_staging
WHERE building_name IS NOT NULL
  AND TRIM(building_name) != ''
  -- Option B: must have phone OR website
  AND (contact_phone IS NOT NULL OR contact_website IS NOT NULL)
ORDER BY
    LOWER(TRIM(REGEXP_REPLACE(building_name, '[^\w\s]', '', 'g'))),
    contact_phone DESC NULLS LAST
ON CONFLICT (name_normalized) DO UPDATE SET
    phone   = COALESCE(EXCLUDED.phone,   leads.phone),
    email   = COALESCE(EXCLUDED.email,   leads.email),
    website = COALESCE(EXCLUDED.website, leads.website),
    score   = GREATEST(EXCLUDED.score,   leads.score),
    updated_at = NOW();


-- ── SOURCE 2: google_places_leads → agencies ─────────────────────
-- Only where phone OR website exists
INSERT INTO leads (
    name, phone, website,
    area, lead_type, source, score, status, name_normalized
)
SELECT DISTINCT ON (
    LOWER(TRIM(REGEXP_REPLACE(business_name, '[^\w\s]', '', 'g')))
)
    business_name,
    phone,
    website,
    area,
    'agency',
    'google_places',
    LEAST(
        COALESCE(review_count, 0) * 0.15
        + COALESCE(rating, 0) * 5,
        100
    ),
    'new',
    LOWER(TRIM(REGEXP_REPLACE(business_name, '[^\w\s]', '', 'g')))
FROM google_places_leads
WHERE business_name IS NOT NULL
  AND TRIM(business_name) != ''
  AND business_name NOT ILIKE '%sponsored%'
  AND business_name NOT ILIKE '%buyrentkenya%'
  -- Option B: must have phone OR website
  AND (phone IS NOT NULL OR website IS NOT NULL)
ORDER BY
    LOWER(TRIM(REGEXP_REPLACE(business_name, '[^\w\s]', '', 'g'))),
    phone DESC NULLS LAST
ON CONFLICT (name_normalized) DO UPDATE SET
    phone   = COALESCE(EXCLUDED.phone,   leads.phone),
    website = COALESCE(EXCLUDED.website, leads.website),
    score   = GREATEST(EXCLUDED.score,   leads.score),
    updated_at = NOW();


-- ── SOURCE 3: listing_staging → agencies + landlords ─────────────
-- DISTINCT ON name_normalized first to avoid duplicate conflicts
-- Only where phone OR website exists
INSERT INTO leads (
    name, owner_name, phone, email, website,
    area, lead_type, source, score, status, name_normalized
)
SELECT DISTINCT ON (
    LOWER(TRIM(REGEXP_REPLACE(
        COALESCE(owner_name, property_name, ''),
        '[^\w\s]', '', 'g'
    )))
)
    COALESCE(owner_name, property_name)  AS name,
    owner_name,
    owner_phone,
    owner_email,
    owner_website,
    area,
    CASE
        WHEN owner_type IN ('agency', 'pm') THEN 'agency'
        ELSE 'landlord'
    END,
    source,
    LEAST(
        COUNT(*) OVER (
            PARTITION BY LOWER(TRIM(COALESCE(owner_name, '')))
        ) * 8.0
        + CASE WHEN owner_phone IS NOT NULL THEN 20 ELSE 0 END
        + CASE WHEN owner_email IS NOT NULL THEN 10 ELSE 0 END,
        100
    ),
    'new',
    LOWER(TRIM(REGEXP_REPLACE(
        COALESCE(owner_name, property_name, ''),
        '[^\w\s]', '', 'g'
    )))
FROM listing_staging
WHERE COALESCE(owner_name, property_name) IS NOT NULL
  AND TRIM(COALESCE(owner_name, property_name, '')) != ''
  -- Option B: must have phone OR website
  AND (owner_phone IS NOT NULL OR owner_website IS NOT NULL)
ORDER BY
    LOWER(TRIM(REGEXP_REPLACE(
        COALESCE(owner_name, property_name, ''),
        '[^\w\s]', '', 'g'
    ))),
    owner_phone DESC NULLS LAST
ON CONFLICT (name_normalized) DO UPDATE SET
    phone   = COALESCE(EXCLUDED.phone,   leads.phone),
    email   = COALESCE(EXCLUDED.email,   leads.email),
    website = COALESCE(EXCLUDED.website, leads.website),
    score   = GREATEST(EXCLUDED.score,   leads.score),
    updated_at = NOW();


-- ── VERIFY ────────────────────────────────────────────────────────
SELECT
    lead_type,
    COUNT(*)                        AS total,
    COUNT(phone)                    AS has_phone,
    COUNT(website)                  AS has_website,
    ROUND(AVG(score)::numeric, 1)   AS avg_score
FROM leads
GROUP BY lead_type
ORDER BY total DESC;