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

-- Running upgrade 0004 -> 0005

CREATE TABLE users (
    id SERIAL NOT NULL, 
    email VARCHAR(254) NOT NULL, 
    display_name VARCHAR(80), 
    password_hash VARCHAR(255), 
    google_sub VARCHAR(255), 
    is_admin BOOLEAN DEFAULT false NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    password_changed_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    last_login_at TIMESTAMP WITHOUT TIME ZONE, 
    disabled_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id), 
    UNIQUE (email), 
    UNIQUE (google_sub)
);

INSERT INTO users (id, email, display_name, password_hash, is_admin,
                           created_at, updated_at, password_changed_at)
        SELECT id, lower(email), display_name, password_hash, true,
               created_at, updated_at, password_changed_at
          FROM owner_account;

INSERT INTO users (email, is_admin)
        SELECT 'unclaimed@localhost', true
         WHERE NOT EXISTS (SELECT 1 FROM users)
           AND (EXISTS (SELECT 1 FROM raw_emails) OR EXISTS (SELECT 1 FROM vip_contacts)
                OR EXISTS (SELECT 1 FROM settings WHERE section <> 'server_state'));

SELECT setval(pg_get_serial_sequence('users', 'id'), coalesce(max(id), 1)) FROM users;

DROP TABLE owner_account;

CREATE TABLE system_state (
    key VARCHAR(64) NOT NULL, 
    value JSONB DEFAULT '{}'::jsonb NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc') NOT NULL, 
    PRIMARY KEY (key)
);

INSERT INTO system_state (key, value, updated_at) SELECT section, value, updated_at FROM settings WHERE section = 'server_state';

DELETE FROM settings WHERE section = 'server_state';

DELETE FROM sessions;

ALTER TABLE sessions ADD COLUMN user_id INTEGER NOT NULL;

ALTER TABLE sessions ADD CONSTRAINT fk_sessions_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

CREATE INDEX ix_sessions_user_id ON sessions (user_id);

ALTER TABLE raw_emails ADD COLUMN user_id INTEGER;

ALTER TABLE vip_contacts ADD COLUMN user_id INTEGER;

ALTER TABLE commitments ADD COLUMN user_id INTEGER;

ALTER TABLE sync_log ADD COLUMN user_id INTEGER;

ALTER TABLE tags ADD COLUMN user_id INTEGER;

ALTER TABLE email_tags ADD COLUMN user_id INTEGER;

ALTER TABLE saved_views ADD COLUMN user_id INTEGER;

ALTER TABLE sent_messages ADD COLUMN user_id INTEGER;

ALTER TABLE events ADD COLUMN user_id INTEGER;

ALTER TABLE jobs ADD COLUMN user_id INTEGER;

ALTER TABLE job_attempts ADD COLUMN user_id INTEGER;

ALTER TABLE notifications ADD COLUMN user_id INTEGER;

ALTER TABLE settings ADD COLUMN user_id INTEGER;

ALTER TABLE calendar_flags ADD COLUMN user_id INTEGER;

UPDATE raw_emails SET user_id = (SELECT min(id) FROM users);

UPDATE vip_contacts SET user_id = (SELECT min(id) FROM users);

UPDATE commitments SET user_id = (SELECT min(id) FROM users);

UPDATE sync_log SET user_id = (SELECT min(id) FROM users);

UPDATE tags SET user_id = (SELECT min(id) FROM users);

UPDATE email_tags SET user_id = (SELECT min(id) FROM users);

UPDATE saved_views SET user_id = (SELECT min(id) FROM users);

UPDATE sent_messages SET user_id = (SELECT min(id) FROM users);

UPDATE events SET user_id = (SELECT min(id) FROM users) WHERE type NOT LIKE 'system.%';

UPDATE jobs SET user_id = (SELECT min(id) FROM users);

UPDATE job_attempts SET user_id = (SELECT user_id FROM jobs WHERE jobs.id = job_attempts.job_id);

UPDATE notifications SET user_id = (SELECT min(id) FROM users);

UPDATE settings SET user_id = (SELECT min(id) FROM users);

UPDATE calendar_flags SET user_id = (SELECT min(id) FROM users);

ALTER TABLE raw_emails ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE raw_emails ADD CONSTRAINT fk_raw_emails_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

DROP INDEX ix_raw_emails_message_id;

ALTER TABLE raw_emails ADD CONSTRAINT uq_raw_emails_user_message UNIQUE (user_id, message_id);

ALTER TABLE vip_contacts ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE vip_contacts ADD CONSTRAINT fk_vip_contacts_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE vip_contacts DROP CONSTRAINT uq_vip_value_type;

ALTER TABLE vip_contacts ADD CONSTRAINT uq_vip_value_type UNIQUE (user_id, match_value, match_type);

ALTER TABLE commitments ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE commitments ADD CONSTRAINT fk_commitments_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

CREATE INDEX ix_commitments_user_id ON commitments (user_id);

ALTER TABLE sync_log ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE sync_log ADD CONSTRAINT fk_sync_log_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

CREATE INDEX ix_sync_log_user_id ON sync_log (user_id);

ALTER TABLE tags ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE tags ADD CONSTRAINT fk_tags_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE tags DROP CONSTRAINT tags_name_key;

ALTER TABLE tags ADD CONSTRAINT uq_tags_user_name UNIQUE (user_id, name);

ALTER TABLE email_tags ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE email_tags ADD CONSTRAINT fk_email_tags_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

CREATE INDEX ix_email_tags_user_id ON email_tags (user_id);

ALTER TABLE saved_views ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE saved_views ADD CONSTRAINT fk_saved_views_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE saved_views DROP CONSTRAINT saved_views_name_key;

ALTER TABLE saved_views ADD CONSTRAINT uq_saved_views_user_name UNIQUE (user_id, name);

ALTER TABLE sent_messages ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE sent_messages ADD CONSTRAINT fk_sent_messages_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE sent_messages DROP CONSTRAINT sent_messages_message_id_key;

ALTER TABLE sent_messages ADD CONSTRAINT uq_sent_messages_user_message UNIQUE (user_id, message_id);

ALTER TABLE events ADD CONSTRAINT fk_events_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

CREATE INDEX ix_events_user_id ON events (user_id);

ALTER TABLE jobs ADD CONSTRAINT fk_jobs_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE jobs DROP CONSTRAINT jobs_idempotency_key_key;

ALTER TABLE jobs ADD CONSTRAINT uq_jobs_user_key UNIQUE NULLS NOT DISTINCT (user_id, idempotency_key);

ALTER TABLE job_attempts ADD CONSTRAINT fk_job_attempts_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

CREATE INDEX ix_job_attempts_user_id ON job_attempts (user_id);

ALTER TABLE notifications ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE notifications ADD CONSTRAINT fk_notifications_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

CREATE INDEX ix_notifications_user_id ON notifications (user_id);

ALTER TABLE notifications DROP CONSTRAINT uq_notification_event_kind;

ALTER TABLE notifications ADD CONSTRAINT uq_notification_event_kind UNIQUE (user_id, event_id, kind);

ALTER TABLE settings ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE settings ADD CONSTRAINT fk_settings_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE settings DROP CONSTRAINT settings_pkey;

ALTER TABLE settings ADD CONSTRAINT settings_pkey PRIMARY KEY (user_id, section);

ALTER TABLE calendar_flags ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE calendar_flags ADD CONSTRAINT fk_calendar_flags_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE calendar_flags DROP CONSTRAINT calendar_flags_dedupe_key_key;

ALTER TABLE calendar_flags ADD CONSTRAINT uq_calendar_flags_user_key UNIQUE (user_id, dedupe_key);

CREATE FUNCTION app_user_id() RETURNS integer LANGUAGE sql STABLE AS
        $$ SELECT nullif(current_setting('app.user_id', true), '')::integer $$;

DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'commitmail_tenant') THEN
                CREATE ROLE commitmail_tenant NOLOGIN;
            END IF;
        END $$;

GRANT USAGE ON SCHEMA public TO commitmail_tenant;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO commitmail_tenant;

GRANT SELECT, INSERT, UPDATE, DELETE ON raw_emails TO commitmail_tenant;

GRANT SELECT, INSERT, UPDATE, DELETE ON vip_contacts TO commitmail_tenant;

GRANT SELECT, INSERT, UPDATE, DELETE ON commitments TO commitmail_tenant;

GRANT SELECT, INSERT, UPDATE, DELETE ON sync_log TO commitmail_tenant;

GRANT SELECT, INSERT, UPDATE, DELETE ON tags TO commitmail_tenant;

GRANT SELECT, INSERT, UPDATE, DELETE ON email_tags TO commitmail_tenant;

GRANT SELECT, INSERT, UPDATE, DELETE ON saved_views TO commitmail_tenant;

GRANT SELECT, INSERT, UPDATE, DELETE ON sent_messages TO commitmail_tenant;

GRANT SELECT, INSERT, DELETE ON events TO commitmail_tenant;

GRANT SELECT, INSERT, UPDATE, DELETE ON jobs TO commitmail_tenant;

GRANT SELECT, DELETE ON job_attempts TO commitmail_tenant;

GRANT SELECT, INSERT, UPDATE, DELETE ON notifications TO commitmail_tenant;

GRANT SELECT, INSERT, UPDATE, DELETE ON settings TO commitmail_tenant;

GRANT SELECT, INSERT, UPDATE, DELETE ON calendar_flags TO commitmail_tenant;

ALTER TABLE raw_emails ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE raw_emails ENABLE ROW LEVEL SECURITY;

ALTER TABLE raw_emails FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON raw_emails TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

ALTER TABLE vip_contacts ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE vip_contacts ENABLE ROW LEVEL SECURITY;

ALTER TABLE vip_contacts FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON vip_contacts TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

ALTER TABLE commitments ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE commitments ENABLE ROW LEVEL SECURITY;

ALTER TABLE commitments FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON commitments TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

ALTER TABLE sync_log ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE sync_log ENABLE ROW LEVEL SECURITY;

ALTER TABLE sync_log FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON sync_log TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

ALTER TABLE tags ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE tags ENABLE ROW LEVEL SECURITY;

ALTER TABLE tags FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON tags TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

ALTER TABLE email_tags ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE email_tags ENABLE ROW LEVEL SECURITY;

ALTER TABLE email_tags FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON email_tags TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

ALTER TABLE saved_views ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE saved_views ENABLE ROW LEVEL SECURITY;

ALTER TABLE saved_views FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON saved_views TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

ALTER TABLE sent_messages ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE sent_messages ENABLE ROW LEVEL SECURITY;

ALTER TABLE sent_messages FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON sent_messages TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

ALTER TABLE events ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE events ENABLE ROW LEVEL SECURITY;

ALTER TABLE events FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON events TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

ALTER TABLE jobs ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;

ALTER TABLE jobs FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON jobs TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

ALTER TABLE job_attempts ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE job_attempts ENABLE ROW LEVEL SECURITY;

ALTER TABLE job_attempts FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON job_attempts TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

ALTER TABLE notifications ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

ALTER TABLE notifications FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON notifications TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

ALTER TABLE settings ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE settings ENABLE ROW LEVEL SECURITY;

ALTER TABLE settings FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON settings TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

ALTER TABLE calendar_flags ALTER COLUMN user_id SET DEFAULT app_user_id();

ALTER TABLE calendar_flags ENABLE ROW LEVEL SECURITY;

ALTER TABLE calendar_flags FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON calendar_flags TO commitmail_tenant USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());

UPDATE alembic_version SET version_num='0005' WHERE alembic_version.version_num = '0004';

COMMIT;

