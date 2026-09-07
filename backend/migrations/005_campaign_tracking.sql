ALTER TABLE campaign_recipients
    ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS opened_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS clicked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS bounced_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS open_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS click_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS bounce_reason TEXT,
    ADD COLUMN IF NOT EXISTS last_event_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS resend_webhook_events (
    id BIGSERIAL PRIMARY KEY,
    svix_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    resend_id TEXT,
    event_created_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_resend_webhook_events_resend_id
    ON resend_webhook_events (resend_id);
