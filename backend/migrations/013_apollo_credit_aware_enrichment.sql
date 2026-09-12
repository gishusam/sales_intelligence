ALTER TABLE apollo_search_runs
    ADD COLUMN IF NOT EXISTS status VARCHAR NOT NULL DEFAULT 'queued',
    ADD COLUMN IF NOT EXISTS processed_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS no_contact_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS failed_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS queued_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS credit_status JSONB,
    ADD COLUMN IF NOT EXISTS billing_cycle_reset_at VARCHAR,
    ADD COLUMN IF NOT EXISTS assigned_to VARCHAR,
    ADD COLUMN IF NOT EXISTS enrichment_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS enrichment_completed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

ALTER TABLE apollo_search_run_prospects
    ADD COLUMN IF NOT EXISTS contact_id INTEGER
        REFERENCES apollo_prospect_contacts(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS status VARCHAR NOT NULL DEFAULT 'queued',
    ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_error VARCHAR;

ALTER TABLE apollo_search_run_prospects
    DROP CONSTRAINT IF EXISTS uq_apollo_search_run_prospect;

DELETE FROM apollo_search_run_prospects duplicate
USING apollo_search_run_prospects canonical
WHERE duplicate.search_run_id = canonical.search_run_id
  AND duplicate.prospect_id = canonical.prospect_id
  AND duplicate.id > canonical.id;

ALTER TABLE apollo_search_run_prospects
    ADD CONSTRAINT uq_apollo_search_run_prospect
    UNIQUE (search_run_id, prospect_id);

CREATE INDEX IF NOT EXISTS apollo_search_run_queue_idx
    ON apollo_search_run_prospects(search_run_id, status, id);
