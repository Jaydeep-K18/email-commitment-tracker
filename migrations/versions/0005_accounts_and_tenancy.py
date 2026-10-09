"""Accounts, and every row belonging to one of them.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08

The app becomes multi-user: a hosted private beta, where each person's Google
sign-in decides whose mailbox it reads. That makes keeping one person's mail
away from another the property everything else rests on, so it is enforced by
the database, not left to each query:

- ``owner_account`` becomes ``users``. The existing owner keeps id 1 and
  becomes the admin, and every existing row is theirs.
- Every per-user table gets ``user_id``. Unique keys become per user: two people
  can have received the same Message-ID, or both have a tag called "Work".
- Row-level security (Postgres only): request and job code runs as the
  ``commitmail_tenant`` role with ``app.user_id`` set for the transaction, and
  sees, inserts and changes only that user's rows. With no user set it sees
  nothing at all, so a forgotten context fails closed rather than open.
  ``user_id`` defaults to the current user, so inserts need not name it.
- ``events``, ``jobs`` and ``job_attempts`` allow NULL: the deployment's own
  events and housekeeping, which only an admin sees.
- The notifier's cursor moves from ``settings`` (now per user) to the new
  ``system_state``.

Sessions are cleared: each needs an owner now, and signing in again is cheap.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_ROLE = "commitmail_tenant"

#: Postgres's own names for the constraints 0002 created without one. SQLite
#: reflects them unnamed; this convention gives them the same names there.
NAMING = {"uq": "%(table_name)s_%(column_0_name)s_key", "pk": "%(table_name)s_pkey"}

#: table -> (user_id nullable, index user_id on its own)
OWNED = {
    "raw_emails": (False, False),
    "vip_contacts": (False, False),
    "commitments": (False, True),
    "sync_log": (False, True),
    "tags": (False, False),
    "email_tags": (False, True),
    "saved_views": (False, False),
    "sent_messages": (False, False),
    "events": (True, True),
    "jobs": (True, False),
    "job_attempts": (True, True),
    "notifications": (False, True),
    "settings": (False, False),
    "calendar_flags": (False, False),
}

#: Old single-column unique key -> the per-user key that replaces it.
UNIQUE_KEYS = {
    "vip_contacts": ("uq_vip_value_type", "uq_vip_value_type", ["match_value", "match_type"]),
    "tags": ("tags_name_key", "uq_tags_user_name", ["name"]),
    "saved_views": ("saved_views_name_key", "uq_saved_views_user_name", ["name"]),
    "sent_messages": ("sent_messages_message_id_key", "uq_sent_messages_user_message", ["message_id"]),
    "jobs": ("jobs_idempotency_key_key", "uq_jobs_user_key", ["idempotency_key"]),
    "calendar_flags": ("calendar_flags_dedupe_key_key", "uq_calendar_flags_user_key", ["dedupe_key"]),
    "notifications": ("uq_notification_event_kind", "uq_notification_event_kind", ["event_id", "kind"]),
}

#: What the tenant role may do to each table. Absent tables (users, sessions,
#: system_state, metrics, heartbeats) are reachable only by the system role.
GRANTS = {
    **{table: "SELECT, INSERT, UPDATE, DELETE" for table in OWNED},
    "events": "SELECT, INSERT, DELETE",
    "job_attempts": "SELECT, DELETE",
}


def is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def utc_now() -> sa.TextClause:
    return sa.text("(now() at time zone 'utc')") if is_postgres() else sa.text("CURRENT_TIMESTAMP")


def json_type():
    return postgresql.JSONB() if is_postgres() else sa.JSON()


def empty_json() -> sa.TextClause:
    return sa.text("'{}'::jsonb") if is_postgres() else sa.text("'{}'")


OWNER = "(SELECT min(id) FROM users)"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("display_name", sa.String(80), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("google_sub", sa.String(255), nullable=True, unique=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("password_changed_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        """
        INSERT INTO users (id, email, display_name, password_hash, is_admin,
                           created_at, updated_at, password_changed_at)
        SELECT id, lower(email), display_name, password_hash, true,
               created_at, updated_at, password_changed_at
          FROM owner_account
        """
    )
    # Mail migrated in before anyone signed up still needs an owner. The first
    # account created claims this placeholder (apps/server/src/auth/routes.ts).
    op.execute(
        """
        INSERT INTO users (email, is_admin)
        SELECT 'unclaimed@localhost', true
         WHERE NOT EXISTS (SELECT 1 FROM users)
           AND (EXISTS (SELECT 1 FROM raw_emails) OR EXISTS (SELECT 1 FROM vip_contacts)
                OR EXISTS (SELECT 1 FROM settings WHERE section <> 'server_state'))
        """
    )
    if is_postgres():
        # The copied owner kept id 1; the next account must not collide with it.
        op.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), coalesce(max(id), 1)) FROM users")
    op.drop_table("owner_account")

    op.create_table(
        "system_state",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", json_type(), nullable=False, server_default=empty_json()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=utc_now()),
    )
    op.execute(
        "INSERT INTO system_state (key, value, updated_at) "
        "SELECT section, value, updated_at FROM settings WHERE section = 'server_state'"
    )
    op.execute("DELETE FROM settings WHERE section = 'server_state'")

    op.execute("DELETE FROM sessions")
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=False))
        batch.create_foreign_key("fk_sessions_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_sessions_user_id", ["user_id"])

    for table in OWNED:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))

    # Everything that exists is the owner's, except the deployment's own events.
    for table in OWNED:
        if table == "events":
            op.execute(f"UPDATE events SET user_id = {OWNER} WHERE type NOT LIKE 'system.%'")
        elif table == "job_attempts":
            op.execute("UPDATE job_attempts SET user_id = (SELECT user_id FROM jobs WHERE jobs.id = job_attempts.job_id)")
        else:
            op.execute(f"UPDATE {table} SET user_id = {OWNER}")

    for table, (nullable, indexed) in OWNED.items():
        with op.batch_alter_table(table, naming_convention=NAMING) as batch:
            if not nullable:
                batch.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
            batch.create_foreign_key(f"fk_{table}_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE")
            if indexed:
                batch.create_index(f"ix_{table}_user_id", ["user_id"])
            if table == "raw_emails":
                batch.drop_index("ix_raw_emails_message_id")
                batch.create_unique_constraint("uq_raw_emails_user_message", ["user_id", "message_id"])
            if table == "settings":
                batch.drop_constraint("settings_pkey", type_="primary")
                batch.create_primary_key("settings_pkey", ["user_id", "section"])
            if table in UNIQUE_KEYS:
                old, new, columns = UNIQUE_KEYS[table]
                batch.drop_constraint(old, type_="unique")
                if table == "jobs" and is_postgres():
                    # A system job (no user) must still not be queued twice.
                    batch.create_unique_constraint(new, ["user_id", *columns], postgresql_nulls_not_distinct=True)
                else:
                    batch.create_unique_constraint(new, ["user_id", *columns])

    if is_postgres():
        _row_level_security()


def _row_level_security() -> None:
    op.execute(
        """
        CREATE FUNCTION app_user_id() RETURNS integer LANGUAGE sql STABLE AS
        $$ SELECT nullif(current_setting('app.user_id', true), '')::integer $$
        """
    )
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{TENANT_ROLE}') THEN
                CREATE ROLE {TENANT_ROLE} NOLOGIN;
            END IF;
        END $$
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {TENANT_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {TENANT_ROLE}")
    for table, privileges in GRANTS.items():
        op.execute(f"GRANT {privileges} ON {table} TO {TENANT_ROLE}")
    for table in OWNED:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN user_id SET DEFAULT app_user_id()")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # Even the table's owner is held to the policies, unless it is a
        # superuser or has BYPASSRLS — which only the system connection has.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} TO {TENANT_ROLE} "
            "USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id())"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM users")).scalar() > 1:
        raise RuntimeError("Cannot go back to a single-owner schema: there is more than one account.")

    if is_postgres():
        for table in OWNED:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} ALTER COLUMN user_id DROP DEFAULT")
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {TENANT_ROLE}")
        op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {TENANT_ROLE}")
        op.execute(f"REVOKE ALL ON SCHEMA public FROM {TENANT_ROLE}")
        op.execute(f"DROP ROLE IF EXISTS {TENANT_ROLE}")
        op.execute("DROP FUNCTION IF EXISTS app_user_id()")

    for table, (nullable, indexed) in OWNED.items():
        with op.batch_alter_table(table, naming_convention=NAMING) as batch:
            if table in UNIQUE_KEYS:
                old, new, columns = UNIQUE_KEYS[table]
                batch.drop_constraint(new, type_="unique")
                batch.create_unique_constraint(old, columns)
            if table == "settings":
                batch.drop_constraint("settings_pkey", type_="primary")
                batch.create_primary_key("settings_pkey", ["section"])
            if table == "raw_emails":
                batch.drop_constraint("uq_raw_emails_user_message", type_="unique")
                batch.create_index("ix_raw_emails_message_id", ["message_id"], unique=True)
            if indexed:
                batch.drop_index(f"ix_{table}_user_id")
            batch.drop_constraint(f"fk_{table}_user_id", type_="foreignkey")
            batch.drop_column("user_id")

    with op.batch_alter_table("sessions") as batch:
        batch.drop_index("ix_sessions_user_id")
        batch.drop_constraint("fk_sessions_user_id", type_="foreignkey")
        batch.drop_column("user_id")

    op.execute(
        "INSERT INTO settings (section, value, updated_at) "
        "SELECT key, value, updated_at FROM system_state WHERE key = 'server_state'"
    )
    op.drop_table("system_state")

    op.create_table(
        "owner_account",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.Column("password_changed_at", sa.DateTime(), nullable=False, server_default=utc_now()),
        sa.CheckConstraint("id = 1", name="ck_owner_account_single"),
    )
    op.execute(
        """
        INSERT INTO owner_account (id, email, display_name, password_hash,
                                   created_at, updated_at, password_changed_at)
        SELECT 1, email, display_name, password_hash, created_at, updated_at, password_changed_at
          FROM users WHERE password_hash IS NOT NULL
        """
    )
    op.drop_table("users")
