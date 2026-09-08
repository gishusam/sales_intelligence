CREATE TABLE IF NOT EXISTS apollo_search_runs (
    id                  SERIAL PRIMARY KEY,
    filters             JSONB,
    found_count         INTEGER DEFAULT 0,
    qualified_count     INTEGER DEFAULT 0,
    approved_count      INTEGER DEFAULT 0,
    rejected_count      INTEGER DEFAULT 0,
    imported_count      INTEGER DEFAULT 0,
    credits_used        INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS apollo_search_run_prospects (
    id              SERIAL PRIMARY KEY,
    search_run_id   INTEGER NOT NULL
                    REFERENCES apollo_search_runs(id) ON DELETE CASCADE,
    prospect_id     INTEGER NOT NULL
                    REFERENCES apollo_prospects(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS apollo_search_run_prospects_run_idx
    ON apollo_search_run_prospects(search_run_id);

CREATE INDEX IF NOT EXISTS apollo_search_run_prospects_prospect_idx
    ON apollo_search_run_prospects(prospect_id);
