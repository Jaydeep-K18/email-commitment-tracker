"""v2 foundation: smart inbox, events, jobs, notifications, settings, auth.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05

Three things here exist only on Postgres, because they are Postgres features the
Node server relies on and SQLite has no equivalent:

* ``raw_emails.search_vector`` — a generated full-text column with a GIN index,
  so inbox search is a ranked index lookup rather than a ``LIKE`` scan.
* A trigger that ``NOTIFY``s on every new event, so the server can push events
  to the browser in real time even when Kafka is not running.
* Partial indexes covering exactly the rows the outbox relay and the job queue
  poll for, which stay small however large the tables grow.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def utc_now() -> sa.TextClause:
    if is_postgres():
        return sa.text("(now() at time zone 'utc')")
    return sa.text("CURRENT_TIMESTAMP")


def json_type():
    return postgresql.JSONB() if is_postgres() else sa.JSON()


def big_id():
    return sa.BigInteger() if is_postgres() else sa.Integer()


def empty_json() -> sa.TextClause:
    return sa.text("'{}'::jsonb") if is_postgres() else sa.text("'{}'")


def upgrade() -> None:
    # --- Smart inbox columns on the existing table -------------------------
    with op.batch_alter_table("raw_emails") as batch:
        batch.add_column(
            sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("is_starred", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("category", sa.String(), nullable=True))
        batch.add_column(sa.Column("category_source", sa.String(), nullable=True))
        batch.add_column(sa.Column("category_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("in_reply_to", sa.String(), nullable=True))
        batch.add_column(sa.Column("cc", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("is_bulk", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("has_invite", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column(
                "has_attachments", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
    op.create_index("ix_raw_emails_category", "raw_emails", ["category"])

    # --- Tags and saved views ----------------------------------------------
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("color", sa.String(16), nullable=False, server_default="slate"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
    )
    op.create_table(
        "email_tags",
        sa.Column(
            "email_id",
            sa.Integer(),
            sa.ForeignKey("raw_emails.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
    )
    op.create_index("ix_email_tags_tag_id", "email_tags", ["tag_id"])

    op.create_table(
        "saved_views",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("filters", json_type(), nullable=False, server_default=empty_json()),
        sa.Column("sort", json_type(), nullable=False, server_default=empty_json()),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=utc_now()),
    )

    # --- Sent-folder metadata ----------------------------------------------
    op.create_table(
        "sent_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.String(), nullable=False, unique=True),
        sa.Column("in_reply_to", sa.String(), nullable=True),
        sa.Column("recipient_email", sa.String(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False, server_default=utc_now()),
    )
    op.create_index("ix_sent_messages_in_reply_to", "sent_messages", ["in_reply_to"])

    # --- Events: audit trail, system log and outbox ------------------------
    op.create_table(
        "events",
        sa.Column("id", big_id(), primary_key=True, autoincrement=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=True),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", json_type(), nullable=False, server_default=empty_json()),
        sa.Column("source", sa.String(16), nullable=False, server_default="worker"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("published_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_events_type", "events", ["type"])
    op.create_index("ix_events_correlation_id", "events", ["correlation_id"])
    op.create_index("ix_events_created_at", "events", ["created_at"])
    op.create_index("ix_events_entity", "events", ["entity_type", "entity_id"])

    # --- Jobs and their retry history --------------------------------------
    op.create_table(
        "jobs",
        sa.Column("id", big_id(), primary_key=True, autoincrement=True),
        sa.Column("type", sa.String(48), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("payload", json_type(), nullable=False, server_default=empty_json()),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", json_type(), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("locked_by", sa.String(64), nullable=True),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_jobs_type", "jobs", ["type"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_next_attempt_at", "jobs", ["next_attempt_at"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])

    op.create_table(
        "job_attempts",
        sa.Column("id", big_id(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            big_id(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("worker", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.UniqueConstraint("job_id", "attempt", name="uq_job_attempt"),
    )
    op.create_index("ix_job_attempts_job_id", "job_attempts", ["job_id"])

    # --- Notifications -----------------------------------------------------
    op.create_table(
        "notifications",
        sa.Column("id", big_id(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("link", sa.String(200), nullable=True),
        sa.Column(
            "event_id",
            big_id(),
            sa.ForeignKey("events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.UniqueConstraint("event_id", "kind", name="uq_notification_event_kind"),
    )
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])

    # --- Settings and the single owner account -----------------------------
    op.create_table(
        "settings",
        sa.Column("section", sa.String(32), primary_key=True),
        sa.Column("value", json_type(), nullable=False, server_default=empty_json()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=utc_now()),
    )
    op.create_table(
        "owner_account",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column(
            "password_changed_at", sa.DateTime(), nullable=False, server_default=utc_now()
        ),
        sa.CheckConstraint("id = 1", name="ck_owner_account_single"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    # --- Calendar intelligence and metrics ---------------------------------
    op.create_table(
        "calendar_flags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "commitment_id",
            sa.Integer(),
            sa.ForeignKey("commitments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "other_commitment_id",
            sa.Integer(),
            sa.ForeignKey("commitments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("external_event_id", sa.String(), nullable=True),
        sa.Column("details", json_type(), nullable=False, server_default=empty_json()),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("dedupe_key", sa.String(160), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_calendar_flags_kind", "calendar_flags", ["kind"])
    op.create_index("ix_calendar_flags_commitment_id", "calendar_flags", ["commitment_id"])

    op.create_table(
        "metric_snapshots",
        sa.Column("id", big_id(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("window", sa.String(8), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("window_end", sa.DateTime(), nullable=False),
        sa.Column("metrics", json_type(), nullable=False, server_default=empty_json()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.UniqueConstraint("source", "window", "window_start", name="uq_metric_window"),
    )
    op.create_index("ix_metric_snapshots_window_end", "metric_snapshots", ["window_end"])

    op.create_table(
        "service_heartbeats",
        sa.Column("service", sa.String(32), primary_key=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("details", json_type(), nullable=False, server_default=empty_json()),
    )

    if is_postgres():
        _postgres_only_upgrade()


def _postgres_only_upgrade() -> None:
    # Weighted so a match in the subject outranks one in the sender, which
    # outranks one buried in the body. 'english'::regconfig must be explicit:
    # a generated column needs an immutable expression, and to_tsvector is only
    # immutable when it is told which configuration to use.
    op.execute(
        """
        ALTER TABLE raw_emails ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english'::regconfig, coalesce(subject, '')), 'A') ||
            setweight(to_tsvector('english'::regconfig,
                coalesce(sender_name, '') || ' ' || coalesce(sender_email, '')), 'B') ||
            setweight(to_tsvector('english'::regconfig, coalesce(body_text, '')), 'C')
        ) STORED
        """
    )
    op.execute(
        "CREATE INDEX ix_raw_emails_search_vector ON raw_emails USING GIN (search_vector)"
    )

    # The relay only ever asks for unpublished events, and the queue only for
    # runnable jobs. Partial indexes hold just those rows.
    op.execute(
        "CREATE INDEX ix_events_unpublished ON events (id) WHERE published_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_jobs_runnable ON jobs (next_attempt_at) "
        "WHERE status IN ('queued', 'retrying')"
    )

    # The payload carries only the id and type: NOTIFY payloads are capped at
    # 8000 bytes, and a listener that needs the full row can read it.
    op.execute(
        """
        CREATE FUNCTION notify_event() RETURNS trigger AS $$
        BEGIN
            PERFORM pg_notify(
                'events',
                json_build_object('id', NEW.id, 'type', NEW.type)::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER events_notify AFTER INSERT ON events "
        "FOR EACH ROW EXECUTE FUNCTION notify_event()"
    )


def downgrade() -> None:
    if is_postgres():
        op.execute("DROP TRIGGER IF EXISTS events_notify ON events")
        op.execute("DROP FUNCTION IF EXISTS notify_event()")

    for table in (
        "service_heartbeats",
        "metric_snapshots",
        "calendar_flags",
        "sessions",
        "owner_account",
        "settings",
        "notifications",
        "job_attempts",
        "jobs",
        "events",
        "sent_messages",
        "saved_views",
        "email_tags",
        "tags",
    ):
        op.drop_table(table)

    op.drop_index("ix_raw_emails_category", table_name="raw_emails")
    if is_postgres():
        op.execute("DROP INDEX IF EXISTS ix_raw_emails_search_vector")
        op.execute("ALTER TABLE raw_emails DROP COLUMN IF EXISTS search_vector")
    with op.batch_alter_table("raw_emails") as batch:
        for column in (
            "has_attachments",
            "has_invite",
            "is_bulk",
            "cc",
            "in_reply_to",
            "category_reason",
            "category_source",
            "category",
            "deleted_at",
            "archived_at",
            "is_starred",
            "is_read",
        ):
            batch.drop_column(column)
