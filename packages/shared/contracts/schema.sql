BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

CREATE TABLE raw_emails (
    id SERIAL NOT NULL, 
    message_id VARCHAR NOT NULL, 
    thread_id VARCHAR, 
    sender_email VARCHAR, 
    sender_name VARCHAR, 
    recipient_email VARCHAR, 
    subject TEXT, 
    body_text TEXT, 
    received_at TIMESTAMP WITHOUT TIME ZONE, 
    vip_tier VARCHAR, 
    processed BOOLEAN DEFAULT false NOT NULL, 
    notification_seen BOOLEAN DEFAULT false NOT NULL, 
    fetched_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_raw_emails_message_id ON raw_emails (message_id);

CREATE TABLE vip_contacts (
    id SERIAL NOT NULL, 
    match_value VARCHAR NOT NULL, 
    match_type VARCHAR NOT NULL, 
    tier VARCHAR NOT NULL, 
    display_name VARCHAR, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_vip_value_type UNIQUE (match_value, match_type)
);

CREATE INDEX ix_vip_contacts_match_value ON vip_contacts (match_value);

CREATE TABLE commitments (
    id SERIAL NOT NULL, 
    email_id INTEGER NOT NULL, 
    type VARCHAR NOT NULL, 
    subject TEXT NOT NULL, 
    deadline TIMESTAMP WITHOUT TIME ZONE, 
    counterparty_name VARCHAR, 
    counterparty_email VARCHAR, 
    direction VARCHAR, 
    evidence_quote TEXT NOT NULL, 
    confidence FLOAT DEFAULT '0' NOT NULL, 
    vip_tier VARCHAR, 
    status VARCHAR DEFAULT 'pending' NOT NULL, 
    calendar_synced BOOLEAN DEFAULT false NOT NULL, 
    ics_uid VARCHAR, 
    gcal_event_id VARCHAR, 
    manually_added BOOLEAN DEFAULT false NOT NULL, 
    sync_approved BOOLEAN DEFAULT false NOT NULL, 
    supersedes_id INTEGER, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(email_id) REFERENCES raw_emails (id) ON DELETE CASCADE, 
    FOREIGN KEY(supersedes_id) REFERENCES commitments (id) ON DELETE SET NULL
);

CREATE INDEX ix_commitments_email_id ON commitments (email_id);

CREATE INDEX ix_commitments_type ON commitments (type);

CREATE TABLE sync_log (
    id SERIAL NOT NULL, 
    commitment_id INTEGER NOT NULL, 
    action VARCHAR NOT NULL, 
    status VARCHAR DEFAULT 'success' NOT NULL, 
    synced_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    error_message TEXT, 
    PRIMARY KEY (id), 
    FOREIGN KEY(commitment_id) REFERENCES commitments (id) ON DELETE CASCADE
);

CREATE INDEX ix_sync_log_commitment_id ON sync_log (commitment_id);

INSERT INTO alembic_version (version_num) VALUES ('0001') RETURNING alembic_version.version_num;

-- Running upgrade 0001 -> 0002

ALTER TABLE raw_emails ADD COLUMN is_read BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE raw_emails ADD COLUMN is_starred BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE raw_emails ADD COLUMN archived_at TIMESTAMP WITHOUT TIME ZONE;

ALTER TABLE raw_emails ADD COLUMN deleted_at TIMESTAMP WITHOUT TIME ZONE;

ALTER TABLE raw_emails ADD COLUMN category VARCHAR;

ALTER TABLE raw_emails ADD COLUMN category_source VARCHAR;

ALTER TABLE raw_emails ADD COLUMN category_reason TEXT;

ALTER TABLE raw_emails ADD COLUMN in_reply_to VARCHAR;

ALTER TABLE raw_emails ADD COLUMN cc TEXT;

ALTER TABLE raw_emails ADD COLUMN is_bulk BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE raw_emails ADD COLUMN has_invite BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE raw_emails ADD COLUMN has_attachments BOOLEAN DEFAULT false NOT NULL;

CREATE INDEX ix_raw_emails_category ON raw_emails (category);

CREATE TABLE tags (
    id SERIAL NOT NULL, 
    name VARCHAR(64) NOT NULL, 
    color VARCHAR(16) DEFAULT 'slate' NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (name)
);

CREATE TABLE email_tags (
    email_id INTEGER NOT NULL, 
    tag_id INTEGER NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    PRIMARY KEY (email_id, tag_id), 
    FOREIGN KEY(email_id) REFERENCES raw_emails (id) ON DELETE CASCADE, 
    FOREIGN KEY(tag_id) REFERENCES tags (id) ON DELETE CASCADE
);

CREATE INDEX ix_email_tags_tag_id ON email_tags (tag_id);

CREATE TABLE saved_views (
    id SERIAL NOT NULL, 
    name VARCHAR(80) NOT NULL, 
    filters JSONB DEFAULT '{}'::jsonb NOT NULL, 
    sort JSONB DEFAULT '{}'::jsonb NOT NULL, 
    is_pinned BOOLEAN DEFAULT false NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (name)
);

CREATE TABLE sent_messages (
    id SERIAL NOT NULL, 
    message_id VARCHAR NOT NULL, 
    in_reply_to VARCHAR, 
    recipient_email VARCHAR, 
    sent_at TIMESTAMP WITHOUT TIME ZONE, 
    fetched_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (message_id)
);

CREATE INDEX ix_sent_messages_in_reply_to ON sent_messages (in_reply_to);

CREATE TABLE events (
    id BIGSERIAL NOT NULL, 
    type VARCHAR(64) NOT NULL, 
    entity_type VARCHAR(32), 
    entity_id VARCHAR(64), 
    correlation_id VARCHAR(64), 
    severity VARCHAR(16) DEFAULT 'info' NOT NULL, 
    message TEXT NOT NULL, 
    payload JSONB DEFAULT '{}'::jsonb NOT NULL, 
    source VARCHAR(16) DEFAULT 'worker' NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    published_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_events_type ON events (type);

CREATE INDEX ix_events_correlation_id ON events (correlation_id);

CREATE INDEX ix_events_created_at ON events (created_at);

CREATE INDEX ix_events_entity ON events (entity_type, entity_id);

CREATE TABLE jobs (
    id BIGSERIAL NOT NULL, 
    type VARCHAR(48) NOT NULL, 
    idempotency_key VARCHAR(160) NOT NULL, 
    payload JSONB DEFAULT '{}'::jsonb NOT NULL, 
    status VARCHAR(16) DEFAULT 'queued' NOT NULL, 
    priority INTEGER DEFAULT '0' NOT NULL, 
    attempts INTEGER DEFAULT '0' NOT NULL, 
    max_attempts INTEGER DEFAULT '5' NOT NULL, 
    next_attempt_at TIMESTAMP WITHOUT TIME ZONE, 
    last_error TEXT, 
    result JSONB, 
    correlation_id VARCHAR(64), 
    locked_by VARCHAR(64), 
    locked_until TIMESTAMP WITHOUT TIME ZONE, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    started_at TIMESTAMP WITHOUT TIME ZONE, 
    finished_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id), 
    UNIQUE (idempotency_key)
);

CREATE INDEX ix_jobs_type ON jobs (type);

CREATE INDEX ix_jobs_status ON jobs (status);

CREATE INDEX ix_jobs_next_attempt_at ON jobs (next_attempt_at);

CREATE INDEX ix_jobs_created_at ON jobs (created_at);

CREATE TABLE job_attempts (
    id BIGSERIAL NOT NULL, 
    job_id BIGINT NOT NULL, 
    attempt INTEGER NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    error TEXT, 
    worker VARCHAR(64), 
    started_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    finished_at TIMESTAMP WITHOUT TIME ZONE, 
    duration_ms INTEGER, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_job_attempt UNIQUE (job_id, attempt), 
    FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE INDEX ix_job_attempts_job_id ON job_attempts (job_id);

CREATE TABLE notifications (
    id BIGSERIAL NOT NULL, 
    kind VARCHAR(32) NOT NULL, 
    title VARCHAR(200) NOT NULL, 
    body TEXT, 
    severity VARCHAR(16) DEFAULT 'info' NOT NULL, 
    link VARCHAR(200), 
    event_id BIGINT, 
    read_at TIMESTAMP WITHOUT TIME ZONE, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_notification_event_kind UNIQUE (event_id, kind), 
    FOREIGN KEY(event_id) REFERENCES events (id) ON DELETE SET NULL
);

CREATE INDEX ix_notifications_created_at ON notifications (created_at);

CREATE TABLE settings (
    section VARCHAR(32) NOT NULL, 
    value JSONB DEFAULT '{}'::jsonb NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    PRIMARY KEY (section)
);

CREATE TABLE owner_account (
    id INTEGER NOT NULL, 
    email VARCHAR(254) NOT NULL, 
    display_name VARCHAR(80), 
    password_hash VARCHAR(255) NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    password_changed_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT ck_owner_account_single CHECK (id = 1)
);

CREATE TABLE sessions (
    id VARCHAR(64) NOT NULL, 
    csrf_token VARCHAR(64) NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    last_seen_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    user_agent VARCHAR(255), 
    ip VARCHAR(64), 
    PRIMARY KEY (id)
);

CREATE INDEX ix_sessions_expires_at ON sessions (expires_at);

CREATE TABLE calendar_flags (
    id SERIAL NOT NULL, 
    kind VARCHAR(16) NOT NULL, 
    commitment_id INTEGER NOT NULL, 
    other_commitment_id INTEGER, 
    external_event_id VARCHAR, 
    details JSONB DEFAULT '{}'::jsonb NOT NULL, 
    status VARCHAR(16) DEFAULT 'open' NOT NULL, 
    dedupe_key VARCHAR(160) NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    resolved_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(commitment_id) REFERENCES commitments (id) ON DELETE CASCADE, 
    FOREIGN KEY(other_commitment_id) REFERENCES commitments (id) ON DELETE CASCADE, 
    UNIQUE (dedupe_key)
);

CREATE INDEX ix_calendar_flags_kind ON calendar_flags (kind);

CREATE INDEX ix_calendar_flags_commitment_id ON calendar_flags (commitment_id);

CREATE TABLE metric_snapshots (
    id BIGSERIAL NOT NULL, 
    source VARCHAR(16) NOT NULL, 
    "window" VARCHAR(8) NOT NULL, 
    window_start TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    window_end TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    metrics JSONB DEFAULT '{}'::jsonb NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_metric_window UNIQUE (source, "window", window_start)
);

CREATE INDEX ix_metric_snapshots_window_end ON metric_snapshots (window_end);

CREATE TABLE service_heartbeats (
    service VARCHAR(32) NOT NULL, 
    last_seen_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    details JSONB DEFAULT '{}'::jsonb NOT NULL, 
    PRIMARY KEY (service)
);

ALTER TABLE raw_emails ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english'::regconfig, coalesce(subject, '')), 'A') ||
            setweight(to_tsvector('english'::regconfig,
                coalesce(sender_name, '') || ' ' || coalesce(sender_email, '')), 'B') ||
            setweight(to_tsvector('english'::regconfig, coalesce(body_text, '')), 'C')
        ) STORED;

CREATE INDEX ix_raw_emails_search_vector ON raw_emails USING GIN (search_vector);

CREATE INDEX ix_events_unpublished ON events (id) WHERE published_at IS NULL;

CREATE INDEX ix_jobs_runnable ON jobs (next_attempt_at) WHERE status IN ('queued', 'retrying');

CREATE FUNCTION notify_event() RETURNS trigger AS $$
        BEGIN
            PERFORM pg_notify(
                'events',
                json_build_object('id', NEW.id, 'type', NEW.type)::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

CREATE TRIGGER events_notify AFTER INSERT ON events FOR EACH ROW EXECUTE FUNCTION notify_event();

UPDATE alembic_version SET version_num='0002' WHERE alembic_version.version_num = '0001';

-- Running upgrade 0002 -> 0003

ALTER TABLE commitments ADD COLUMN gcal_synced_hash VARCHAR(32);

UPDATE alembic_version SET version_num='0003' WHERE alembic_version.version_num = '0002';

-- Running upgrade 0003 -> 0004

ALTER TABLE raw_emails DROP COLUMN notification_seen;

UPDATE alembic_version SET version_num='0004' WHERE alembic_version.version_num = '0003';

COMMIT;

