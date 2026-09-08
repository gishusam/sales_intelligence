CREATE TABLE IF NOT EXISTS apollo_prospects (
    id                      SERIAL PRIMARY KEY,
    apollo_organization_id  TEXT,
    name                    TEXT NOT NULL,
    normalized_name         TEXT,
    domain                  TEXT,
    website_url             TEXT,
    linkedin_url            TEXT,
    employee_count          INTEGER,
    city                    TEXT,
    country                 TEXT,
    industry                TEXT,
    keywords                JSONB,
    quality_score           DOUBLE PRECISION DEFAULT 0,
    quality_band            TEXT,
    score_breakdown         JSONB,
    score_reasons           JSONB,
    review_status           TEXT NOT NULL DEFAULT 'discovered',
    imported_lead_id        INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    first_seen_at           TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at            TIMESTAMPTZ DEFAULT NOW(),
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS apollo_prospect_contacts (
    id                  SERIAL PRIMARY KEY,
    prospect_id         INTEGER NOT NULL
                        REFERENCES apollo_prospects(id) ON DELETE CASCADE,
    apollo_person_id    TEXT,
    first_name          TEXT,
    last_name           TEXT,
    name                TEXT,
    title               TEXT,
    seniority           TEXT,
    linkedin_url        TEXT,
    email               TEXT,
    phone               TEXT,
    enrichment_status   TEXT NOT NULL DEFAULT 'not_enriched',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS apollo_prospects_org_id_idx
    ON apollo_prospects(apollo_organization_id);

CREATE INDEX IF NOT EXISTS apollo_prospects_domain_idx
    ON apollo_prospects(domain);

CREATE INDEX IF NOT EXISTS apollo_prospects_normalized_name_idx
    ON apollo_prospects(normalized_name);

CREATE INDEX IF NOT EXISTS apollo_prospect_contacts_prospect_idx
    ON apollo_prospect_contacts(prospect_id);

CREATE INDEX IF NOT EXISTS apollo_prospect_contacts_person_idx
    ON apollo_prospect_contacts(apollo_person_id);
