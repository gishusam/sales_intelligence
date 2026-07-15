-- Nyumba Zetu Sales Intelligence — Full Schema
-- Run this once on any new database to set up everything
-- Last updated: July 2026

-- ── Users ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id                   SERIAL PRIMARY KEY,
    name                 TEXT NOT NULL,
    email                TEXT NOT NULL UNIQUE,
    password_hash        TEXT NOT NULL,
    role                 TEXT NOT NULL DEFAULT 'sales',
    is_active            BOOLEAN DEFAULT TRUE,
    must_change_password BOOLEAN DEFAULT FALSE,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);

-- ── Leads — production table ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS leads (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    owner_name          TEXT,
    owner_type          TEXT,
    phone               TEXT,
    email               TEXT,
    website             TEXT,
    area                TEXT,
    lead_type           TEXT,
    source              TEXT,
    source_url          TEXT,
    score               FLOAT DEFAULT 0.0,
    status              TEXT DEFAULT 'new',
    lead_quality        TEXT,
    assigned_to         TEXT,
    last_contacted      TIMESTAMPTZ,
    contact_attempts    INTEGER DEFAULT 0,
    follow_up_date      DATE,
    email_sent_at       TIMESTAMPTZ,
    ai_score            TEXT,
    ai_score_reason     TEXT,
    ai_scored_at        TIMESTAMPTZ,
    contact_person      TEXT,
    contact_person_role TEXT,
    promoted_at         TIMESTAMPTZ DEFAULT NOW(),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS leads_source_url_idx
    ON leads(source_url) WHERE source_url IS NOT NULL;
CREATE INDEX IF NOT EXISTS leads_area_idx        ON leads(area);
CREATE INDEX IF NOT EXISTS leads_lead_type_idx   ON leads(lead_type);
CREATE INDEX IF NOT EXISTS leads_status_idx      ON leads(status);
CREATE INDEX IF NOT EXISTS leads_score_idx       ON leads(score DESC);
CREATE INDEX IF NOT EXISTS leads_follow_up_idx   ON leads(follow_up_date)
    WHERE follow_up_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS leads_ai_score_idx    ON leads(ai_score)
    WHERE ai_score IS NOT NULL;

-- ── Lead notes ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lead_notes (
    id               SERIAL PRIMARY KEY,
    lead_id          INTEGER NOT NULL,
    note             TEXT NOT NULL,
    created_by       TEXT,
    ai_score         TEXT,
    ai_score_reason  TEXT,
    follow_up_days   INTEGER,
    signals          TEXT[],
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS lead_notes_lead_id_idx  ON lead_notes(lead_id);
CREATE INDEX IF NOT EXISTS lead_notes_created_idx  ON lead_notes(created_at DESC);

-- ── Lead events — audit trail ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS lead_events (
    id          SERIAL PRIMARY KEY,
    lead_id     INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    from_value  TEXT,
    to_value    TEXT,
    changed_by  TEXT,
    note        TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS lead_events_lead_id_idx ON lead_events(lead_id);

-- ── Scraper runs ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scraper_runs (
    id               SERIAL PRIMARY KEY,
    scraper_type     TEXT NOT NULL,
    areas            TEXT[],
    status           TEXT NOT NULL DEFAULT 'running',
    records_found    INTEGER DEFAULT 0,
    with_contacts    INTEGER DEFAULT 0,
    imported         INTEGER DEFAULT 0,
    updated          INTEGER DEFAULT 0,
    duplicates       INTEGER DEFAULT 0,
    rejected         INTEGER DEFAULT 0,
    error            TEXT,
    started_at       TIMESTAMPTZ DEFAULT NOW(),
    finished_at      TIMESTAMPTZ,
    duration_seconds FLOAT,
    started_by       TEXT
);

-- ── Scraper run records — per-record audit ────────────────────────
CREATE TABLE IF NOT EXISTS scraper_run_records (
    id         SERIAL PRIMARY KEY,
    run_id     INTEGER,
    name       TEXT,
    area       TEXT,
    phone      TEXT,
    website    TEXT,
    category   TEXT,
    outcome    TEXT NOT NULL,
    reason     TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS srr_run_id_idx  ON scraper_run_records(run_id);
CREATE INDEX IF NOT EXISTS srr_outcome_idx ON scraper_run_records(outcome);

-- ── Staging tables — raw scraped data ────────────────────────────
CREATE TABLE IF NOT EXISTS listing_staging (
    id               SERIAL PRIMARY KEY,
    property_name    TEXT,
    property_type    TEXT,
    bedrooms         INTEGER,
    price_kes        INTEGER,
    raw_price        TEXT,
    area             TEXT,
    raw_address      TEXT,
    owner_name       TEXT,
    owner_type       TEXT,
    owner_phone      TEXT,
    owner_email      TEXT,
    owner_website    TEXT,
    listing_date     DATE,
    listing_age_days INTEGER,
    score            FLOAT DEFAULT 0,
    priority         TEXT DEFAULT 'COLD',
    source           TEXT NOT NULL DEFAULT 'buyrentkenya',
    source_url       TEXT UNIQUE,
    source_id        TEXT,
    scraped_at       TIMESTAMPTZ DEFAULT NOW(),
    promoted         BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS google_places_leads (
    id            SERIAL PRIMARY KEY,
    business_name TEXT NOT NULL,
    area          TEXT,
    address       TEXT,
    phone         TEXT,
    website       TEXT,
    category      TEXT,
    search_query  TEXT,
    maps_url      TEXT,
    scraped_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(business_name, area)
);

CREATE TABLE IF NOT EXISTS apartment_staging (
    id                 SERIAL PRIMARY KEY,
    building_name      TEXT NOT NULL,
    normalized_name    TEXT,
    search_area        TEXT,
    search_query       TEXT,
    category           TEXT,
    maps_url           TEXT,
    latitude           FLOAT,
    longitude          FLOAT,
    lead_score         INTEGER DEFAULT 0,
    score_reasons      TEXT,
    contact_phone      TEXT,
    contact_email      TEXT,
    contact_website    TEXT,
    management_company TEXT,
    social_media       TEXT,
    enrichment_status  TEXT DEFAULT 'pending',
    confidence         TEXT DEFAULT 'low',
    source             TEXT DEFAULT 'google_maps',
    scraped_at         TIMESTAMPTZ DEFAULT NOW(),
    enriched_at        TIMESTAMPTZ,
    UNIQUE(normalized_name, search_area)
);

CREATE TABLE IF NOT EXISTS developer_staging (
    id                SERIAL PRIMARY KEY,
    developer_name    TEXT NOT NULL,
    membership_tier   TEXT,
    tier_score        INTEGER,
    contact_phone     TEXT,
    contact_email     TEXT,
    contact_website   TEXT,
    address           TEXT,
    maps_url          TEXT,
    enrichment_status TEXT DEFAULT 'pending',
    enriched_at       TIMESTAMPTZ,
    source            TEXT DEFAULT 'kpda_directory',
    scraped_at        TIMESTAMPTZ DEFAULT NOW(),
    area              TEXT,
    CONSTRAINT dev_staging_name_uniq UNIQUE (developer_name)
);
