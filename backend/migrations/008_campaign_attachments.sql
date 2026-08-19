ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS attachment_content BYTEA,
    ADD COLUMN IF NOT EXISTS attachment_mime_type TEXT,
    ADD COLUMN IF NOT EXISTS attachment_size INTEGER;
