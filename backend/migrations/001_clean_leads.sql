-- ================================================================
-- Step 1: Migration — drop old leads table, create clean one
-- Step 2: Promotion — pull from all 3 staging tables into leads
--
-- Run this once:
--   docker exec -i nzetu_db psql -U nzetu -d nzetu_db < migration_and_promotion.sql
-- ================================================================


-- ── STEP 1: DROP OLD TABLE AND CREATE CLEAN ONE ──────────────────

DROP TABLE IF EXISTS leads CASCADE;

CREATE TABLE leads (
    id               SERIAL PRIMARY KEY,

    -- Who to contact
    name             TEXT NOT NULL,
    owner_name       TEXT,
    phone            TEXT,
    email            TEXT,
    website          TEXT,

    -- Where they are
    area             TEXT,

    -- What type of lead
    lead_type        TEXT NOT NULL,   -- apartment / agency / landlord
    source           TEXT NOT NULL,   -- google_maps / buyrentkenya / jiji / google_places

    -- Sales workflow
    score            FLOAT   DEFAULT 0.0,
    status           TEXT    DEFAULT 'new',
    notes            TEXT,
    assigned_to      TEXT,
    last_contacted   TIMESTAMPTZ,

    -- Dedup key — prevents same lead appearing twice
    name_normalized  TEXT UNIQUE NOT NULL,

    -- Timestamps
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes the sales team and API will actually use
CREATE INDEX idx_leads_lead_type  ON leads(lead_type);
CREATE INDEX idx_leads_status     ON leads(status);
CREATE INDEX idx_leads_area       ON leads(area);
CREATE INDEX idx_leads_score      ON leads(score DESC);
CREATE INDEX idx_leads_assigned   ON leads(assigned_to);


-- ── STEP 2: PROMOTE FROM STAGING TABLES ──────────────────────────

-- ── Source 1: apartment_staging → lead_type = 'apartment' ────────
-- These are residential buildings from Google Maps.
-- Sales pitch: "Let us manage your building"
INSERT INTO leads (
    name, owner_name, phone, email, website,
    area, lead_type, source,
    score, status, name_normalized
)
SELECT
    building_name,
    management_company,           -- who manages it if known
    contact_phone,
    contact_email,
    contact_website,
    search_area,
    'apartment',
    'google_maps',
    COALESCE(lead_score, 0.0),
    'new',
    -- dedup key: normalized name + area
    LOWER(TRIM(REGEXP_REPLACE(building_name, '[^\w\s]', '', 'g')))
    || '::' || LOWER(TRIM(COALESCE(search_area, 'unknown')))
FROM apartment_staging
WHERE building_name IS NOT NULL
  AND TRIM(building_name) != ''
ON CONFLICT (name_normalized) DO UPDATE SET
    phone    = COALESCE(EXCLUDED.phone,   leads.phone),
    email    = COALESCE(EXCLUDED.email,   leads.email),
    website  = COALESCE(EXCLUDED.website, leads.website),
    score    = EXCLUDED.score,
    updated_at = NOW();


-- ── Source 2: google_places_leads → lead_type = 'agency' ─────────
-- These are property management companies from Google Places.
-- Sales pitch: "Partner with us or we'll compete with you"
INSERT INTO leads (
    name, phone, website,
    area, lead_type, source,
    score, status, name_normalized
)
SELECT
    business_name,
    phone,
    website,
    area,
    'agency',
    'google_places',
    -- Score based on review count and rating
    LEAST(
        COALESCE(review_count, 0) * 0.15
        + COALESCE(rating, 0) * 5,
        100
    ),
    'new',
    LOWER(TRIM(REGEXP_REPLACE(business_name, '[^\w\s]', '', 'g')))
    || '::' || LOWER(TRIM(COALESCE(area, 'unknown')))
FROM google_places_leads
WHERE business_name IS NOT NULL
  AND TRIM(business_name) != ''
  -- Exclude noise records
  AND business_name NOT ILIKE '%sponsored%'
  AND business_name NOT ILIKE '%buyrentkenya%'
ON CONFLICT (name_normalized) DO UPDATE SET
    phone    = COALESCE(EXCLUDED.phone,   leads.phone),
    website  = COALESCE(EXCLUDED.website, leads.website),
    score    = EXCLUDED.score,
    updated_at = NOW();


-- ── Source 3: listing_staging → lead_type = 'landlord' or 'agency' 
-- Listings from BuyRentKenya and Jiji.
-- Agencies with multiple listings → agency leads
-- Individual owners → landlord leads
INSERT INTO leads (
    name, owner_name, phone, email, website,
    area, lead_type, source,
    score, status, name_normalized
)
SELECT
    -- Use owner name as the lead name since we're targeting the owner
    COALESCE(owner_name, property_name)         AS name,
    owner_name,
    owner_phone,
    owner_email,
    owner_website,
    area,
    CASE
        WHEN owner_type = 'agency' THEN 'agency'
        WHEN owner_type = 'pm'     THEN 'agency'
        ELSE 'landlord'
    END                                          AS lead_type,
    source,
    -- Score: agencies with more listings score higher
    LEAST(
        COUNT(*) OVER (PARTITION BY LOWER(TRIM(COALESCE(owner_name, '')))) * 8.0
        + CASE WHEN owner_phone IS NOT NULL THEN 20 ELSE 0 END
        + CASE WHEN owner_email IS NOT NULL THEN 10 ELSE 0 END,
        100
    )                                            AS score,
    'new',
    LOWER(TRIM(REGEXP_REPLACE(
        COALESCE(owner_name, property_name, ''),
        '[^\w\s]', '', 'g'
    )))
    || '::' || LOWER(TRIM(COALESCE(area, 'unknown')))
FROM listing_staging
WHERE COALESCE(owner_name, property_name) IS NOT NULL
  AND TRIM(COALESCE(owner_name, property_name, '')) != ''
ON CONFLICT (name_normalized) DO UPDATE SET
    phone    = COALESCE(EXCLUDED.phone,   leads.phone),
    email    = COALESCE(EXCLUDED.email,   leads.email),
    website  = COALESCE(EXCLUDED.website, leads.website),
    score    = GREATEST(EXCLUDED.score,   leads.score),
    updated_at = NOW();


-- ── STEP 3: VERIFY ────────────────────────────────────────────────
SELECT
    lead_type,
    COUNT(*)                            AS total,
    COUNT(phone)                        AS has_phone,
    COUNT(email)                        AS has_email,
    ROUND(AVG(score)::numeric, 1)       AS avg_score,
    COUNT(*) FILTER (WHERE status = 'new') AS new_leads
FROM leads
GROUP BY lead_type
ORDER BY total DESC;