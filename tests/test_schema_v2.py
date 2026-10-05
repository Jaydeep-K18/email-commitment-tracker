"""The v2 schema: migrations, constraints, and the move from SQLite to Postgres.

The migrations and the ORM models describe the same schema twice, once for
Postgres and once for Python, and nothing forces them to agree except a test.
The Node server reads the migrated schema while the worker reads the models, so
a drift between the two would show up as one language writing a column the
other cannot see.
"""
from __future__ import annotations

import io
from datetime import datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scripts.migrate_sqlite_to_postgres import MigrationError, migrate
from src import config
from src.storage.models import (
    Base,
    Commitment,
    EmailTag,
    Event,
    Job,
    JobAttempt,
    Notification,
    OwnerAccount,
    RawEmail,
    Tag,
)


def alembic_config(url: str, buffer: io.StringIO | None = None) -> Config:
    cfg = Config(str(config.BASE_DIR / "alembic.ini"), output_buffer=buffer)
    cfg.set_main_option("script_location", str(config.BASE_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture()
def sqlite_url(tmp_path):
    return f"sqlite:///{tmp_path / 'schema.db'}"


# --- Migrations ---------------------------------------------------------------

def test_the_migrations_produce_exactly_the_schema_the_models_describe(sqlite_url):
    command.upgrade(alembic_config(sqlite_url), "head")

    engine = create_engine(sqlite_url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)
    engine.dispose()

    assert differences == [], "migrations and models disagree:\n" + "\n".join(
        map(str, differences)
    )


def test_every_migration_can_be_reversed(sqlite_url):
    cfg = alembic_config(sqlite_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(sqlite_url)
    with engine.connect() as connection:
        remaining = connection.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name != 'alembic_version'")
        ).scalars().all()
    engine.dispose()
    assert remaining == []


def postgres_sql() -> str:
    buffer = io.StringIO()
    command.upgrade(
        alembic_config("postgresql+psycopg://user:pw@localhost/db", buffer), "head", sql=True
    )
    return buffer.getvalue()


def test_the_postgres_schema_carries_its_postgres_only_features():
    """These exist only in the migration, so only rendering it can check them."""
    sql = postgres_sql()

    assert "search_vector tsvector" in sql and "USING GIN (search_vector)" in sql
    assert "CREATE TRIGGER events_notify AFTER INSERT ON events" in sql
    assert "WHERE published_at IS NULL" in sql            # outbox relay index
    assert "WHERE status IN ('queued', 'retrying')" in sql  # runnable-jobs index
    assert "JSONB" in sql and "BIGSERIAL" in sql


def test_postgres_defaults_are_utc_not_the_sessions_time_zone():
    """now() alone would be converted using the connection's TimeZone setting."""
    sql = postgres_sql()
    assert "DEFAULT (now() at time zone 'utc')" in sql
    assert "DEFAULT now()" not in sql


# --- Configuration ------------------------------------------------------------

@pytest.mark.parametrize(
    "given,expected",
    [
        ("postgres://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        ("postgresql://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
        ("postgresql+psycopg://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
        ("sqlite:///x.db", "sqlite:///x.db"),
    ],
)
def test_node_and_python_can_share_one_database_url(given, expected):
    assert config.normalize_database_url(given) == expected


# --- Constraints the application relies on -----------------------------------

@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def test_the_same_job_cannot_be_enqueued_twice(session):
    session.add(Job(type="process_email", idempotency_key="process_email:7"))
    session.commit()
    session.add(Job(type="process_email", idempotency_key="process_email:7"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_a_job_keeps_its_attempts_in_order(session):
    job = Job(type="push_google_event", idempotency_key="gcal_push:1:abc")
    job.attempt_history = [
        JobAttempt(attempt=2, status="succeeded"),
        JobAttempt(attempt=1, status="failed", error="timeout"),
    ]
    session.add(job)
    session.commit()
    session.expire_all()

    assert [a.attempt for a in session.get(Job, job.id).attempt_history] == [1, 2]


def test_a_redelivered_event_cannot_notify_twice(session):
    """Kafka delivers at least once; the second notification must be refused."""
    event = Event(type="job.failed", message="Job 3 failed")
    session.add(event)
    session.flush()
    session.add(Notification(kind="job_failed", title="Job failed", event_id=event.id))
    session.commit()
    session.add(Notification(kind="job_failed", title="Job failed", event_id=event.id))
    with pytest.raises(IntegrityError):
        session.commit()


def test_there_can_only_ever_be_one_owner(session):
    session.add(OwnerAccount(id=1, email="me@example.com", password_hash="x"))
    session.commit()
    session.add(OwnerAccount(id=2, email="intruder@example.com", password_hash="y"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_an_email_removes_its_tags_but_not_the_tag(session):
    email = RawEmail(message_id="<t@x>")
    tag = Tag(name="clients")
    session.add_all([email, tag])
    session.flush()
    session.add(EmailTag(email_id=email.id, tag_id=tag.id))
    session.commit()

    session.delete(email)
    session.commit()

    assert session.scalars(select(EmailTag)).all() == []
    assert session.scalars(select(Tag)).one().name == "clients"


def test_inbox_state_defaults_to_unread_unstarred_and_visible(session):
    session.add(RawEmail(message_id="<fresh@x>"))
    session.commit()
    email = session.scalars(select(RawEmail)).one()
    assert (email.is_read, email.is_starred, email.archived_at, email.deleted_at) == (
        False, False, None, None,
    )


# --- SQLite -> Postgres data migration ----------------------------------------

@pytest.fixture()
def populated_source(tmp_path):
    """A v1-era database: emails, commitments with a supersede chain pointing
    *forward* in id order, and commitments already published to Google."""
    url = f"sqlite:///{tmp_path / 'source.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        email = RawEmail(message_id="<m1@x>", subject="Report", received_at=datetime(2026, 8, 1))
        s.add(email)
        s.flush()
        first = Commitment(
            email_id=email.id, type="deadline_on_you", subject="Send report",
            evidence_quote="send it", gcal_event_id="google-abc",
        )
        second = Commitment(
            email_id=email.id, type="deadline_on_you", subject="Send report v2",
            evidence_quote="send it again", gcal_event_id="google-def",
        )
        s.add_all([first, second])
        s.flush()
        # The *older* row points at the newer one: a naive in-order copy would
        # violate the foreign key on the first insert.
        first.supersedes_id = second.id
        s.commit()
    engine.dispose()
    return url


def test_everything_is_copied_with_its_original_ids(populated_source, tmp_path):
    target = f"sqlite:///{tmp_path / 'target.db'}"

    report = migrate(populated_source, target)

    assert report.copied["raw_emails"] == 1
    assert report.copied["commitments"] == 2
    engine = create_engine(target)
    with Session(engine) as s:
        rows = {c.id: c for c in s.scalars(select(Commitment))}
        assert rows[1].supersedes_id == 2
    engine.dispose()


def test_published_google_events_keep_their_ids(populated_source, tmp_path):
    """Losing these would make the next sync duplicate every existing event."""
    report = migrate(populated_source, f"sqlite:///{tmp_path / 'target.db'}")
    assert report.google_event_ids == 2


def test_copying_into_a_database_that_already_has_mail_is_refused(populated_source, tmp_path):
    target = f"sqlite:///{tmp_path / 'target.db'}"
    migrate(populated_source, target)

    with pytest.raises(MigrationError, match="already holds 1 emails"):
        migrate(populated_source, target)


def test_a_database_cannot_be_migrated_onto_itself(populated_source):
    with pytest.raises(MigrationError, match="same database"):
        migrate(populated_source, populated_source)
