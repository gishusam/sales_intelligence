-- Tie staging records to the exact scraper run that produced them.

ALTER TABLE google_places_leads
    ADD COLUMN IF NOT EXISTS run_id INTEGER;

ALTER TABLE apartment_staging
    ADD COLUMN IF NOT EXISTS run_id INTEGER;

ALTER TABLE developer_staging
    ADD COLUMN IF NOT EXISTS run_id INTEGER;


CREATE INDEX IF NOT EXISTS google_places_leads_run_id_idx
    ON google_places_leads(run_id);

CREATE INDEX IF NOT EXISTS apartment_staging_run_id_idx
    ON apartment_staging(run_id);

CREATE INDEX IF NOT EXISTS developer_staging_run_id_idx
    ON developer_staging(run_id);