ALTER TABLE apollo_prospect_contacts
    ADD COLUMN IF NOT EXISTS contact_enrichment_status
        VARCHAR(32) NOT NULL DEFAULT 'not_requested';

ALTER TABLE apollo_prospect_contacts
    ADD COLUMN IF NOT EXISTS contact_enrichment_request_id
        VARCHAR(128);
