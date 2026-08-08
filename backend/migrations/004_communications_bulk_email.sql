-- ── Mailing lists ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mailing_lists (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    created_by  TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Mailing list contacts ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mailing_list_contacts (
    id              SERIAL PRIMARY KEY,
    list_id         INTEGER NOT NULL REFERENCES mailing_lists(id) ON DELETE CASCADE,
    email           TEXT NOT NULL,
    name            TEXT,
    lead_id         INTEGER,  -- if from leads table
    source          TEXT DEFAULT 'manual',  -- manual/leads/csv
    unsubscribed    BOOLEAN DEFAULT FALSE,
    unsubscribed_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(list_id, email)
);

CREATE INDEX IF NOT EXISTS mlc_list_id_idx ON mailing_list_contacts(list_id);
CREATE INDEX IF NOT EXISTS mlc_email_idx   ON mailing_list_contacts(email);

-- ── Campaigns ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaigns (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    sender_name     TEXT NOT NULL DEFAULT 'Nyumba Zetu',
    sender_email    TEXT NOT NULL,
    reply_to        TEXT,
    status          TEXT NOT NULL DEFAULT 'draft',
    -- draft / scheduled / sending / sent / failed
    recipient_type  TEXT NOT NULL,
    -- leads / mailing_list / csv_upload
    recipient_filter JSONB,
    -- e.g. {"lead_type": "agency", "area": "Kilimani", "ai_score": "WARM_PROSPECT"}
    mailing_list_id INTEGER REFERENCES mailing_lists(id),
    attachment_name TEXT,
    attachment_url  TEXT,
    scheduled_at    TIMESTAMPTZ,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    total_recipients INTEGER DEFAULT 0,
    sent_count       INTEGER DEFAULT 0,
    failed_count     INTEGER DEFAULT 0,
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS campaigns_status_idx ON campaigns(status);

-- ── Campaign recipients ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaign_recipients (
    id           SERIAL PRIMARY KEY,
    campaign_id  INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    name         TEXT,
    lead_id      INTEGER,
    status       TEXT DEFAULT 'pending',
    -- pending / sent / failed / bounced / unsubscribed
    resend_id    TEXT,  -- Resend message ID for tracking
    sent_at      TIMESTAMPTZ,
    error        TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS cr_campaign_id_idx ON campaign_recipients(campaign_id);
CREATE INDEX IF NOT EXISTS cr_status_idx      ON campaign_recipients(status);
CREATE INDEX IF NOT EXISTS cr_resend_id_idx   ON campaign_recipients(resend_id);

-- ── Global unsubscribes ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS unsubscribes (
    id             SERIAL PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    unsubscribed_at TIMESTAMPTZ DEFAULT NOW(),
    reason         TEXT
);

-- ── Verify ────────────────────────────────────────────────────────
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
AND table_name IN (
    'mailing_lists', 'mailing_list_contacts',
    'campaigns', 'campaign_recipients', 'unsubscribes'
)
ORDER BY table_name;