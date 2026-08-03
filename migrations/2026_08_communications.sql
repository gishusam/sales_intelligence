-- Nyumba Zetu Communications foundation
-- Additive migration: does not remove or rewrite existing email_outreach data.

BEGIN;

-- ── Sender identities ─────────────────────────────────────────────
-- Provider credentials remain in environment variables / secret storage.
CREATE TABLE IF NOT EXISTS sender_identities (
    id                 SERIAL PRIMARY KEY,
    display_name       TEXT NOT NULL,
    email_address      TEXT NOT NULL,
    reply_to_address   TEXT,
    provider           TEXT NOT NULL DEFAULT 'smtp',
    provider_reference TEXT,
    is_default         BOOLEAN NOT NULL DEFAULT FALSE,
    is_active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT sender_identity_email_not_blank
        CHECK (BTRIM(email_address) <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS sender_identities_email_idx
    ON sender_identities (LOWER(email_address));

CREATE UNIQUE INDEX IF NOT EXISTS sender_identities_one_default_idx
    ON sender_identities (is_default)
    WHERE is_default = TRUE AND is_active = TRUE;


-- ── Reusable communication templates ──────────────────────────────
CREATE TABLE IF NOT EXISTS communication_templates (
    id                    SERIAL PRIMARY KEY,
    slug                  TEXT NOT NULL UNIQUE,
    name                  TEXT NOT NULL,
    template_type         TEXT NOT NULL,
    channel               TEXT NOT NULL DEFAULT 'email',
    subject               TEXT NOT NULL,
    body_text             TEXT NOT NULL,
    body_html             TEXT,
    required_placeholders JSONB NOT NULL DEFAULT '[]'::JSONB,
    version               INTEGER NOT NULL DEFAULT 1,
    is_default            BOOLEAN NOT NULL DEFAULT FALSE,
    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
    created_by            INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_by            INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT communication_template_type_check
        CHECK (template_type IN ('cold', 'followup', 'newsletter')),
    CONSTRAINT communication_template_channel_check
        CHECK (channel = 'email'),
    CONSTRAINT communication_template_subject_not_blank
        CHECK (BTRIM(subject) <> ''),
    CONSTRAINT communication_template_body_not_blank
        CHECK (BTRIM(body_text) <> ''),
    CONSTRAINT communication_template_version_positive
        CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS communication_templates_type_idx
    ON communication_templates (template_type, is_active);

CREATE UNIQUE INDEX IF NOT EXISTS communication_templates_default_idx
    ON communication_templates (template_type)
    WHERE is_default = TRUE AND is_active = TRUE;


-- ── Saved audience definitions ────────────────────────────────────
CREATE TABLE IF NOT EXISTS audience_segments (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    definition  JSONB NOT NULL DEFAULT '{}'::JSONB,
    is_dynamic  BOOLEAN NOT NULL DEFAULT TRUE,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ── Campaigns ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaigns (
    id                   SERIAL PRIMARY KEY,
    name                 TEXT NOT NULL,
    campaign_type        TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'draft',
    sender_identity_id   INTEGER REFERENCES sender_identities(id)
                               ON DELETE SET NULL,
    default_template_id  INTEGER REFERENCES communication_templates(id)
                               ON DELETE SET NULL,
    audience_segment_id  INTEGER REFERENCES audience_segments(id)
                               ON DELETE SET NULL,
    scheduled_at         TIMESTAMPTZ,
    started_at           TIMESTAMPTZ,
    completed_at         TIMESTAMPTZ,
    metadata             JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_by           INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_by           INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT campaign_type_check
        CHECK (campaign_type IN ('cold', 'followup', 'newsletter')),
    CONSTRAINT campaign_status_check
        CHECK (
            status IN (
                'draft',
                'scheduled',
                'running',
                'paused',
                'completed',
                'cancelled',
                'failed'
            )
        )
);

CREATE INDEX IF NOT EXISTS campaigns_status_idx
    ON campaigns (status, scheduled_at);

CREATE INDEX IF NOT EXISTS campaigns_created_by_idx
    ON campaigns (created_by);


-- ── Campaign sequence steps ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaign_steps (
    id               SERIAL PRIMARY KEY,
    campaign_id      INTEGER NOT NULL REFERENCES campaigns(id)
                            ON DELETE CASCADE,
    step_order       INTEGER NOT NULL,
    template_id      INTEGER REFERENCES communication_templates(id)
                            ON DELETE SET NULL,
    delay_minutes    INTEGER NOT NULL DEFAULT 0,
    subject_override TEXT,
    body_override    TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT campaign_step_order_positive
        CHECK (step_order >= 1),
    CONSTRAINT campaign_step_delay_nonnegative
        CHECK (delay_minutes >= 0),
    CONSTRAINT campaign_step_unique_order
        UNIQUE (campaign_id, step_order)
);


-- ── Campaign recipients ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaign_recipients (
    id               SERIAL PRIMARY KEY,
    campaign_id      INTEGER NOT NULL REFERENCES campaigns(id)
                            ON DELETE CASCADE,
    lead_id          INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    recipient_name   TEXT,
    recipient_email  TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    current_step     INTEGER NOT NULL DEFAULT 1,
    next_action_at   TIMESTAMPTZ,
    last_error       TEXT,
    personalization  JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT campaign_recipient_status_check
        CHECK (
            status IN (
                'pending',
                'queued',
                'sent',
                'failed',
                'suppressed',
                'replied',
                'unsubscribed',
                'skipped'
            )
        ),
    CONSTRAINT campaign_recipient_step_positive
        CHECK (current_step >= 1),
    CONSTRAINT campaign_recipient_email_not_blank
        CHECK (BTRIM(recipient_email) <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS campaign_recipient_unique_idx
    ON campaign_recipients (campaign_id, LOWER(recipient_email));

CREATE INDEX IF NOT EXISTS campaign_recipients_next_action_idx
    ON campaign_recipients (status, next_action_at);


-- ── Canonical outbound message record ─────────────────────────────
CREATE TABLE IF NOT EXISTS email_messages (
    id                    SERIAL PRIMARY KEY,
    campaign_id           INTEGER REFERENCES campaigns(id)
                                ON DELETE SET NULL,
    campaign_step_id      INTEGER REFERENCES campaign_steps(id)
                                ON DELETE SET NULL,
    campaign_recipient_id INTEGER REFERENCES campaign_recipients(id)
                                ON DELETE SET NULL,
    lead_id               INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    sender_identity_id    INTEGER REFERENCES sender_identities(id)
                                ON DELETE SET NULL,
    template_id           INTEGER REFERENCES communication_templates(id)
                                ON DELETE SET NULL,
    sent_by               INTEGER REFERENCES users(id) ON DELETE SET NULL,
    message_type          TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'draft',
    idempotency_key       TEXT,
    provider              TEXT,
    provider_message_id   TEXT,
    from_name             TEXT NOT NULL,
    from_email            TEXT NOT NULL,
    to_email              TEXT NOT NULL,
    subject               TEXT NOT NULL,
    body_text             TEXT NOT NULL,
    body_html             TEXT,
    error_message         TEXT,
    queued_at             TIMESTAMPTZ,
    sent_at               TIMESTAMPTZ,
    failed_at             TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT email_message_type_check
        CHECK (
            message_type IN (
                'manual',
                'cold',
                'followup',
                'newsletter'
            )
        ),
    CONSTRAINT email_message_status_check
        CHECK (
            status IN (
                'draft',
                'queued',
                'sending',
                'sent',
                'failed',
                'suppressed',
                'cancelled'
            )
        ),
    CONSTRAINT email_message_from_not_blank
        CHECK (BTRIM(from_email) <> ''),
    CONSTRAINT email_message_to_not_blank
        CHECK (BTRIM(to_email) <> ''),
    CONSTRAINT email_message_subject_not_blank
        CHECK (BTRIM(subject) <> ''),
    CONSTRAINT email_message_body_not_blank
        CHECK (BTRIM(body_text) <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS email_messages_idempotency_idx
    ON email_messages (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS email_messages_provider_id_idx
    ON email_messages (provider, provider_message_id)
    WHERE provider_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS email_messages_lead_idx
    ON email_messages (lead_id, created_at DESC);

CREATE INDEX IF NOT EXISTS email_messages_status_idx
    ON email_messages (status, queued_at);


-- ── Provider and recipient events ─────────────────────────────────
CREATE TABLE IF NOT EXISTS email_events (
    id                SERIAL PRIMARY KEY,
    email_message_id  INTEGER NOT NULL REFERENCES email_messages(id)
                              ON DELETE CASCADE,
    event_type        TEXT NOT NULL,
    provider_event_id TEXT,
    occurred_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload           JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT email_event_type_check
        CHECK (
            event_type IN (
                'queued',
                'sent',
                'delivered',
                'opened',
                'clicked',
                'bounced',
                'complained',
                'unsubscribed',
                'replied',
                'failed'
            )
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS email_events_provider_event_idx
    ON email_events (provider_event_id)
    WHERE provider_event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS email_events_message_idx
    ON email_events (email_message_id, occurred_at DESC);


-- ── Follow-up and drafting automation rules ───────────────────────
CREATE TABLE IF NOT EXISTS automation_rules (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    automation_type     TEXT NOT NULL,
    trigger_type        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft',
    sender_identity_id  INTEGER REFERENCES sender_identities(id)
                              ON DELETE SET NULL,
    template_id         INTEGER REFERENCES communication_templates(id)
                              ON DELETE SET NULL,
    audience_segment_id INTEGER REFERENCES audience_segments(id)
                              ON DELETE SET NULL,
    configuration       JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT automation_type_check
        CHECK (
            automation_type IN (
                'followup',
                'newsletter_draft',
                'campaign_draft'
            )
        ),
    CONSTRAINT automation_status_check
        CHECK (status IN ('draft', 'active', 'paused', 'archived'))
);


CREATE TABLE IF NOT EXISTS automation_executions (
    id                 SERIAL PRIMARY KEY,
    automation_rule_id INTEGER NOT NULL REFERENCES automation_rules(id)
                             ON DELETE CASCADE,
    lead_id            INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    campaign_id        INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    status             TEXT NOT NULL DEFAULT 'pending',
    reason             TEXT,
    output             JSONB NOT NULL DEFAULT '{}'::JSONB,
    executed_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT automation_execution_status_check
        CHECK (
            status IN (
                'pending',
                'draft_created',
                'completed',
                'skipped',
                'failed'
            )
        )
);

CREATE INDEX IF NOT EXISTS automation_executions_rule_idx
    ON automation_executions (automation_rule_id, created_at DESC);


-- ── Global sending suppression ────────────────────────────────────
CREATE TABLE IF NOT EXISTS suppression_list (
    id          SERIAL PRIMARY KEY,
    email       TEXT NOT NULL,
    reason      TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    lead_id     INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    details     JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT suppression_reason_check
        CHECK (
            reason IN (
                'manual',
                'unsubscribed',
                'bounce',
                'complaint',
                'invalid_address'
            )
        ),
    CONSTRAINT suppression_email_not_blank
        CHECK (BTRIM(email) <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS suppression_list_active_email_idx
    ON suppression_list (LOWER(email))
    WHERE is_active = TRUE;


-- ── Newsletter content ingestion ──────────────────────────────────
CREATE TABLE IF NOT EXISTS newsletter_sources (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    source_url    TEXT,
    configuration JSONB NOT NULL DEFAULT '{}'::JSONB,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT newsletter_source_type_check
        CHECK (source_type IN ('rss', 'url', 'manual', 'n8n'))
);


CREATE TABLE IF NOT EXISTS newsletter_drafts (
    id             SERIAL PRIMARY KEY,
    source_id      INTEGER REFERENCES newsletter_sources(id)
                           ON DELETE SET NULL,
    title          TEXT NOT NULL,
    subject        TEXT NOT NULL,
    preview_text   TEXT,
    body_json      JSONB NOT NULL DEFAULT '{}'::JSONB,
    body_html      TEXT,
    body_text      TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'draft',
    source_summary JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    approved_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    scheduled_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT newsletter_draft_status_check
        CHECK (
            status IN (
                'draft',
                'review',
                'approved',
                'scheduled',
                'sent',
                'archived'
            )
        )
);


-- Protect the new tables from direct Supabase public API access.
ALTER TABLE sender_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE communication_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE audience_segments ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_recipients ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE automation_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE automation_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE suppression_list ENABLE ROW LEVEL SECURITY;
ALTER TABLE newsletter_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE newsletter_drafts ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE
    sender_identities,
    communication_templates,
    audience_segments,
    campaigns,
    campaign_steps,
    campaign_recipients,
    email_messages,
    email_events,
    automation_rules,
    automation_executions,
    suppression_list,
    newsletter_sources,
    newsletter_drafts
FROM anon, authenticated;

REVOKE ALL ON ALL SEQUENCES IN SCHEMA public
FROM anon, authenticated;

COMMIT;

-- Canonical manual-message delivery columns
ALTER TABLE email_messages
    ADD COLUMN IF NOT EXISTS template_id BIGINT
        REFERENCES communication_templates(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS sender_identity_id BIGINT
        REFERENCES sender_identities(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS sent_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS recipient_email TEXT,
    ADD COLUMN IF NOT EXISTS recipient_name TEXT,
    ADD COLUMN IF NOT EXISTS body_text TEXT,
    ADD COLUMN IF NOT EXISTS body_html TEXT,
    ADD COLUMN IF NOT EXISTS message_type TEXT NOT NULL DEFAULT 'manual',
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS provider_message_id TEXT,
    ADD COLUMN IF NOT EXISTS attachment_name TEXT,
    ADD COLUMN IF NOT EXISTS attachment_content_type TEXT,
    ADD COLUMN IF NOT EXISTS attachment_size INTEGER,
    ADD COLUMN IF NOT EXISTS follow_up_date DATE,
    ADD COLUMN IF NOT EXISTS error_message TEXT,
    ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS uq_email_messages_idempotency_key
    ON email_messages (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

ALTER TABLE suppression_list
    ADD COLUMN IF NOT EXISTS email_address TEXT,
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS uq_suppression_list_normalized_email
    ON suppression_list (LOWER(email_address))
    WHERE email_address IS NOT NULL;

-- Draft campaign foundation columns
ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS campaign_type TEXT NOT NULL DEFAULT 'cold',
    ADD COLUMN IF NOT EXISTS sender_identity_id BIGINT
        REFERENCES sender_identities(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'draft',
    ADD COLUMN IF NOT EXISTS created_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE campaign_steps
    ADD COLUMN IF NOT EXISTS step_order INTEGER,
    ADD COLUMN IF NOT EXISTS delay_days INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS template_id BIGINT
        REFERENCES communication_templates(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS subject_override TEXT,
    ADD COLUMN IF NOT EXISTS body_text_override TEXT,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE campaign_recipients
    ADD COLUMN IF NOT EXISTS lead_id INTEGER
        REFERENCES leads(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS recipient_email TEXT,
    ADD COLUMN IF NOT EXISTS recipient_name TEXT,
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'enrolled',
    ADD COLUMN IF NOT EXISTS suppression_reason TEXT,
    ADD COLUMN IF NOT EXISTS enrolled_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS enrolled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_campaign_steps_order
    ON campaign_steps (campaign_id, step_order)
    WHERE step_order IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_campaign_recipients_lead
    ON campaign_recipients (campaign_id, lead_id)
    WHERE lead_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_campaign_recipients_email
    ON campaign_recipients (
        campaign_id,
        LOWER(recipient_email)
    )
    WHERE recipient_email IS NOT NULL;

CREATE INDEX IF NOT EXISTS
    idx_campaigns_status_created
    ON campaigns (status, created_at DESC);

CREATE INDEX IF NOT EXISTS
    idx_campaign_recipients_status
    ON campaign_recipients (campaign_id, status);

-- Campaign lifecycle and execution preflight
ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS prepared_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS paused_from_status TEXT;

ALTER TABLE campaign_steps
    ADD COLUMN IF NOT EXISTS snapshot_subject TEXT,
    ADD COLUMN IF NOT EXISTS snapshot_body_text TEXT,
    ADD COLUMN IF NOT EXISTS snapshot_body_html TEXT,
    ADD COLUMN IF NOT EXISTS snapshot_template_version INTEGER;

ALTER TABLE campaign_recipients
    ADD COLUMN IF NOT EXISTS company_name_snapshot TEXT,
    ADD COLUMN IF NOT EXISTS area_snapshot TEXT,
    ADD COLUMN IF NOT EXISTS rep_name_snapshot TEXT,
    ADD COLUMN IF NOT EXISTS rep_email_snapshot TEXT,
    ADD COLUMN IF NOT EXISTS next_step_order INTEGER,
    ADD COLUMN IF NOT EXISTS next_run_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

ALTER TABLE email_messages
    ADD COLUMN IF NOT EXISTS campaign_id BIGINT
        REFERENCES campaigns(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS campaign_recipient_id BIGINT
        REFERENCES campaign_recipients(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS campaign_step_id BIGINT
        REFERENCES campaign_steps(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_email_messages_campaign_recipient_step
    ON email_messages (
        campaign_recipient_id,
        campaign_step_id
    )
    WHERE
        campaign_recipient_id IS NOT NULL
        AND campaign_step_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS
    idx_email_messages_due_campaign_jobs
    ON email_messages (
        status,
        scheduled_for
    )
    WHERE
        message_type = 'campaign'
        AND status = 'queued';

-- Asynchronous delivery worker columns
ALTER TABLE email_messages
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS locked_by TEXT,
    ADD COLUMN IF NOT EXISTS dead_lettered_at TIMESTAMPTZ;

UPDATE email_messages
SET next_attempt_at = COALESCE(
    next_attempt_at,
    scheduled_for,
    created_at
)
WHERE
    status = 'queued'
    AND next_attempt_at IS NULL;

CREATE INDEX IF NOT EXISTS
    idx_email_messages_worker_due
    ON email_messages (
        status,
        next_attempt_at,
        id
    )
    WHERE
        message_type = 'campaign'
        AND status = 'queued';

CREATE INDEX IF NOT EXISTS
    idx_email_messages_stale_processing
    ON email_messages (
        status,
        locked_at
    )
    WHERE status = 'processing';

-- Conservative follow-up automation foundation
ALTER TABLE automation_rules
    ADD COLUMN IF NOT EXISTS name TEXT,
    ADD COLUMN IF NOT EXISTS trigger_type TEXT
        NOT NULL DEFAULT 'inactivity_followup',
    ADD COLUMN IF NOT EXISTS template_id BIGINT
        REFERENCES communication_templates(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS sender_identity_id BIGINT
        REFERENCES sender_identities(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS inactivity_days INTEGER NOT NULL DEFAULT 7,
    ADD COLUMN IF NOT EXISTS max_drafts_per_lead INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS created_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE automation_executions
    ADD COLUMN IF NOT EXISTS rule_id BIGINT
        REFERENCES automation_rules(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS lead_id INTEGER
        REFERENCES leads(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS email_message_id BIGINT
        REFERENCES email_messages(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS status TEXT,
    ADD COLUMN IF NOT EXISTS reason TEXT,
    ADD COLUMN IF NOT EXISTS executed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE email_messages
    ADD COLUMN IF NOT EXISTS automation_rule_id BIGINT
        REFERENCES automation_rules(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS lead_communication_state (
    lead_id INTEGER PRIMARY KEY
        REFERENCES leads(id) ON DELETE CASCADE,
    reply_received_at TIMESTAMPTZ,
    automation_paused BOOLEAN NOT NULL DEFAULT FALSE,
    pause_reason TEXT,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_automation_executions_idempotency_key
    ON automation_executions (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS
    idx_automation_rules_active
    ON automation_rules (is_active, id)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS
    idx_automation_executions_rule_created
    ON automation_executions (rule_id, created_at DESC);

CREATE INDEX IF NOT EXISTS
    idx_email_messages_automation_rule
    ON email_messages (
        automation_rule_id,
        lead_id,
        status
    )
    WHERE message_type = 'automation';

-- Newsletter drafting and audience foundation
ALTER TABLE newsletter_drafts
    ADD COLUMN IF NOT EXISTS name TEXT,
    ADD COLUMN IF NOT EXISTS subject TEXT,
    ADD COLUMN IF NOT EXISTS preview_text TEXT,
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'draft',
    ADD COLUMN IF NOT EXISTS sender_identity_id BIGINT
        REFERENCES sender_identities(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS blocks JSONB NOT NULL DEFAULT '[]'::JSONB,
    ADD COLUMN IF NOT EXISTS body_text TEXT,
    ADD COLUMN IF NOT EXISTS body_html TEXT,
    ADD COLUMN IF NOT EXISTS created_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS reviewed_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS approved_by INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE IF NOT EXISTS newsletter_recipients (
    id BIGSERIAL PRIMARY KEY,
    newsletter_id BIGINT NOT NULL
        REFERENCES newsletter_drafts(id) ON DELETE CASCADE,
    lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    recipient_email TEXT NOT NULL,
    recipient_name TEXT,
    company_name_snapshot TEXT,
    status TEXT NOT NULL DEFAULT 'eligible',
    suppression_reason TEXT,
    added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE email_messages
    ADD COLUMN IF NOT EXISTS newsletter_id BIGINT
        REFERENCES newsletter_drafts(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_newsletter_recipients_email
    ON newsletter_recipients (
        newsletter_id,
        LOWER(recipient_email)
    );

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_newsletter_recipients_lead
    ON newsletter_recipients (
        newsletter_id,
        lead_id
    )
    WHERE lead_id IS NOT NULL;

-- Bulk newsletter scheduling and delivery
ALTER TABLE newsletter_drafts
    ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;

ALTER TABLE newsletter_recipients
    ADD COLUMN IF NOT EXISTS queued_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ;

ALTER TABLE email_messages
    ADD COLUMN IF NOT EXISTS newsletter_recipient_id BIGINT
        REFERENCES newsletter_recipients(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_email_messages_newsletter_recipient
    ON email_messages (newsletter_recipient_id)
    WHERE newsletter_recipient_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS
    idx_newsletter_recipients_delivery_status
    ON newsletter_recipients (newsletter_id, status);
