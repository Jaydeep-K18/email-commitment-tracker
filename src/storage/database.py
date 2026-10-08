"""Database access via SQLAlchemy — PostgreSQL in the app, SQLite in the tests.

Owns the engine/session lifecycle, schema creation, and small helpers used by the
collection layer. Schema: PROJECT_PLAN.md §10, plus the v2 tables in
:mod:`src.storage.models`.

On Postgres the schema is created and evolved by Alembic, never by
``create_all``: the Node server shares these tables, so the schema needs one
versioned owner. SQLite keeps ``create_all`` because every test builds a fresh
throwaway database from the models.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from src import config
from src.events.recorder import EMAIL_RECEIVED, email_correlation, record_event
from src.filtering.vip_filter import (
    SKIP,
    normalize_match_type,
    normalize_match_value,
    normalize_tier,
)
from src.storage.models import Base, Commitment, RawEmail, SyncLog, VipContact

if TYPE_CHECKING:  # avoid a runtime import of higher layers from storage
    from src.collection.email_parser import ParsedEmail
    from src.extraction.schemas import ExtractedCommitment


def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def build_engine(url: str):
    """An engine configured for whichever database ``url`` points at."""
    if is_sqlite(url):
        built = create_engine(url, future=True)
        event.listen(built, "connect", _enable_sqlite_foreign_keys)
        return built
    # pool_pre_ping: the worker holds connections across long LLM calls, and a
    # Postgres restart in the meantime should cost one retry, not a crash.
    return create_engine(url, future=True, pool_pre_ping=True)


def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):  # noqa: ANN001
    """Turn on foreign-key enforcement (SQLite defaults it off)."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


engine = build_engine(config.DATABASE_URL)


# expire_on_commit=False so returned objects remain readable after the session
# closes (the fetcher inspects saved rows outside the transaction).
SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)


#: Columns added to existing tables after their first release, as
#: ``table -> [(column, SQL type and default)]``. ``create_all`` only creates
#: missing *tables*, so a database made by an earlier phase needs these added
#: explicitly. Every entry must be additive and safe to apply to live data.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    # Phase 5. supersedes_id is declared without its REFERENCES clause here:
    # SQLite cannot add a foreign key to an existing table, and the column is
    # only ever written with ids the sync engine just read from this table.
    "commitments": [
        ("sync_approved", "BOOLEAN NOT NULL DEFAULT 0"),
        ("supersedes_id", "INTEGER"),
        # Phase 9.
        ("gcal_event_id", "TEXT"),
        # Phase 10.
        ("manually_added", "BOOLEAN NOT NULL DEFAULT 0"),
        # v2.
        ("gcal_synced_hash", "VARCHAR(32)"),
    ],
    "raw_emails": [
        # v2 smart inbox. Listed here only so a pre-v2 SQLite file keeps working
        # until it is migrated into Postgres; Postgres gets these from Alembic.
        ("is_read", "BOOLEAN NOT NULL DEFAULT 0"),
        ("is_starred", "BOOLEAN NOT NULL DEFAULT 0"),
        ("archived_at", "DATETIME"),
        ("deleted_at", "DATETIME"),
        ("category", "VARCHAR"),
        ("category_source", "VARCHAR"),
        ("category_reason", "TEXT"),
        ("in_reply_to", "VARCHAR"),
        ("cc", "TEXT"),
        ("is_bulk", "BOOLEAN NOT NULL DEFAULT 0"),
        ("has_invite", "BOOLEAN NOT NULL DEFAULT 0"),
        ("has_attachments", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
}

def _existing_columns(connection, table: str) -> set[str]:
    rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _migrate_added_columns() -> None:
    """Add any columns missing from an older database file.

    Additive only — no column is ever dropped, renamed, or retyped, so running
    this against a database holding real commitments cannot lose data.
    """
    inspector = inspect(engine)
    present_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in present_tables:
                continue  # create_all just made it with every column
            existing = _existing_columns(connection, table)
            for column, definition in columns:
                if column in existing:
                    continue
                connection.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )


def upgrade_schema(url: str | None = None) -> None:
    """Bring a Postgres database to the latest Alembic revision."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(str(config.BASE_DIR / "alembic.ini"))
    # Keep the caller's logging; see migrations/env.py.
    alembic_cfg.attributes["configure_logger"] = False
    alembic_cfg.set_main_option("script_location", str(config.BASE_DIR / "migrations"))
    alembic_cfg.set_main_option(
        "sqlalchemy.url", (url or config.DATABASE_URL).replace("%", "%%")
    )
    command.upgrade(alembic_cfg, "head")


def init_db() -> None:
    """Make sure the schema exists and is current."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not is_sqlite(config.DATABASE_URL):
        upgrade_schema()
        return
    Base.metadata.create_all(engine)
    _migrate_added_columns()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session scope: commit on success, roll back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def email_exists(session: Session, message_id: str) -> bool:
    """Return True if an email with this Message-ID is already stored."""
    stmt = select(RawEmail.id).where(RawEmail.message_id == message_id)
    return session.execute(stmt).first() is not None


def email_by_message_id(session: Session, message_id: str) -> RawEmail | None:
    """The stored email with this Message-ID, if there is one.

    ``save_email`` returns None for a duplicate, which is the right answer while
    fetching but not when the caller needs the existing row to attach something
    to — adding a second commitment from the Gmail panel, for instance.
    """
    stmt = select(RawEmail).where(RawEmail.message_id == message_id)
    return session.execute(stmt).scalars().first()


def save_email(
    session: Session,
    parsed: "ParsedEmail",
    vip_tier: str | None = None,
    processed: bool = False,
) -> RawEmail | None:
    """Insert a parsed email, or return ``None`` if it already exists (dedup).

    Dedup key is the Message-ID. The caller supplies the VIP tier (Phase 2) and
    whether the email is already closed out; the extraction pipeline (Phase 3)
    fills in the rest later.
    """
    if not parsed.message_id or email_exists(session, parsed.message_id):
        return None
    email = RawEmail(
        message_id=parsed.message_id,
        thread_id=parsed.thread_id,
        sender_email=parsed.sender_email,
        sender_name=parsed.sender_name,
        recipient_email=parsed.recipient_email,
        subject=parsed.subject,
        body_text=parsed.body_text,
        received_at=parsed.received_at,
        vip_tier=vip_tier,
        processed=processed,
        in_reply_to=parsed.in_reply_to,
        cc=parsed.cc,
        is_bulk=parsed.is_bulk,
        has_invite=parsed.has_invite,
        has_attachments=parsed.has_attachments,
    )
    session.add(email)
    session.flush()  # assign the primary key within this transaction

    sender = parsed.sender_name or parsed.sender_email or "an unknown sender"
    record_event(
        session,
        EMAIL_RECEIVED,
        f"Email from {sender}: {parsed.subject or '(no subject)'}",
        entity_type="email",
        entity_id=email.id,
        correlation_id=email_correlation(email.id),
        payload={
            "sender_email": parsed.sender_email,
            "subject": parsed.subject,
            "vip_tier": vip_tier,
            "will_analyze": not processed,
        },
    )
    return email


# --- VIP contacts (Phase 2) ----------------------------------------------

def add_vip_contact(
    session: Session,
    match_value: str,
    match_type: str,
    tier: str,
    display_name: str | None = None,
) -> VipContact:
    """Create a VIP rule, or update the tier of an existing identical rule.

    Values are validated and normalised by :mod:`src.filtering.vip_filter`, so an
    invalid tier or match type raises ``VipFilterError`` before touching the DB.
    """
    match_type = normalize_match_type(match_type)
    tier = normalize_tier(tier)
    match_value = normalize_match_value(match_value, match_type)

    existing = session.execute(
        select(VipContact).where(
            VipContact.match_value == match_value,
            VipContact.match_type == match_type,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.tier = tier
        if display_name:
            existing.display_name = display_name
        session.flush()
        return existing

    contact = VipContact(
        match_value=match_value,
        match_type=match_type,
        tier=tier,
        display_name=display_name or None,
    )
    session.add(contact)
    session.flush()
    return contact


def list_vip_contacts(session: Session) -> list[VipContact]:
    """Return all VIP rules, most specific match types first."""
    contacts = list(session.execute(select(VipContact)).scalars().all())
    order = {"exact_email": 0, "domain": 1, "name_pattern": 2}
    return sorted(
        contacts,
        key=lambda c: (order.get((c.match_type or "").lower(), 99), c.match_value),
    )


def delete_vip_contact(session: Session, contact_id: int) -> bool:
    """Delete a VIP rule by id. Returns True if a row was removed."""
    contact = session.get(VipContact, contact_id)
    if contact is None:
        return False
    session.delete(contact)
    session.flush()
    return True


# --- Extraction (Phase 3) -------------------------------------------------

def emails_awaiting_extraction(
    session: Session, limit: int | None = None
) -> list[RawEmail]:
    """Return VIP-filtered emails that still need extraction.

    Excludes SKIP-tier and untagged email, so nothing the user does not care
    about is ever sent to the LLM.
    """
    stmt = (
        select(RawEmail)
        .where(
            RawEmail.processed.is_(False),
            RawEmail.vip_tier.is_not(None),
            RawEmail.vip_tier != "SKIP",
        )
        .order_by(RawEmail.received_at.desc(), RawEmail.id.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars().all())


def reset_emails_for_reprocessing(session: Session, limit: int | None = None) -> int:
    """Re-queue already-extracted emails so the pipeline can run on them again.

    Prompt iteration is expected during Phase 3, so re-running extraction over
    the same emails after a prompt change needs to be easy. SKIP-tier email stays
    closed out.
    """
    stmt = select(RawEmail).where(
        RawEmail.processed.is_(True),
        RawEmail.vip_tier.is_not(None),
        RawEmail.vip_tier != "SKIP",
    ).order_by(RawEmail.received_at.desc(), RawEmail.id.desc())
    if limit:
        stmt = stmt.limit(limit)
    emails = list(session.execute(stmt).scalars().all())
    for email in emails:
        email.processed = False
    session.flush()
    return len(emails)


def delete_commitments_for_email(session: Session, email_id: int) -> int:
    """Remove existing commitments for an email (used when re-extracting)."""
    existing = list(
        session.execute(
            select(Commitment).where(Commitment.email_id == email_id)
        ).scalars().all()
    )
    for commitment in existing:
        session.delete(commitment)
    session.flush()
    return len(existing)


def save_commitment(
    session: Session, email: RawEmail, extracted: "ExtractedCommitment"
) -> Commitment:
    """Persist one validated commitment, linked to its source email.

    The counterparty address comes from the email itself rather than the model,
    so it can never be hallucinated.
    """
    commitment = Commitment(
        email_id=email.id,
        type=extracted.type.value,
        subject=extracted.subject,
        deadline=extracted.deadline,
        counterparty_name=extracted.counterparty or email.sender_name,
        counterparty_email=email.sender_email,
        direction=extracted.direction.value if extracted.direction else None,
        evidence_quote=extracted.evidence_quote,
        confidence=extracted.confidence,
        vip_tier=email.vip_tier,
        status="pending",
        calendar_synced=False,
    )
    session.add(commitment)
    session.flush()
    return commitment


# --- Calendar sync (Phase 4) ----------------------------------------------

#: Statuses that permanently take a commitment off the calendar.
_CLOSED_STATUSES = ("dismissed", "superseded")


def calendar_candidates(session: Session) -> list[Commitment]:
    """Commitments that *could* appear on the calendar, before tier logic.

    A coarse query: dated, still open. Deciding which candidates actually get
    published is the sync engine's job (:mod:`src.sync.sync_engine`) — storage
    deliberately holds no policy.
    """
    stmt = (
        select(Commitment)
        .where(
            Commitment.deadline.is_not(None),
            Commitment.status.not_in(_CLOSED_STATUSES),
        )
        .order_by(Commitment.deadline.asc())
    )
    return list(session.execute(stmt).scalars().all())


def open_commitments(session: Session) -> list[Commitment]:
    """Every still-open commitment, dated or not, oldest email first.

    Conflict resolution works over this: a follow-up that *adds* a date to a
    previously open-ended commitment is exactly the case worth catching, so the
    undated ones cannot be filtered out here.
    """
    stmt = (
        select(Commitment)
        .where(Commitment.status.not_in(_CLOSED_STATUSES))
        .order_by(Commitment.created_at.asc(), Commitment.id.asc())
    )
    return list(session.execute(stmt).scalars().all())


def published_commitments(session: Session) -> list[Commitment]:
    """Commitments currently marked as being on the calendar.

    Unlike :func:`calendar_candidates` this ignores status, so a commitment that
    was published and then dismissed or superseded still shows up and can be
    retracted from the feed.
    """
    stmt = select(Commitment).where(Commitment.calendar_synced.is_(True))
    return list(session.execute(stmt).scalars().all())


def commitments_awaiting_approval(session: Session) -> list[Commitment]:
    """Dated, open commitments the user has not yet approved for the calendar.

    Backs the review queue. Whether a given tier actually *needs* approval is
    the sync engine's decision; this is the raw pool it draws from.
    """
    stmt = (
        select(Commitment)
        .where(
            Commitment.deadline.is_not(None),
            Commitment.status.not_in(_CLOSED_STATUSES),
            Commitment.sync_approved.is_(False),
        )
        .order_by(Commitment.deadline.asc())
    )
    return list(session.execute(stmt).scalars().all())


def set_sync_approval(
    session: Session, commitment_id: int, approved: bool
) -> Commitment | None:
    """Approve or un-approve a commitment for the calendar."""
    commitment = session.get(Commitment, commitment_id)
    if commitment is None:
        return None
    commitment.sync_approved = approved
    if not approved:
        # Un-approving must also pull it back out of the published feed.
        commitment.calendar_synced = False
    session.flush()
    return commitment


def set_commitment_status(
    session: Session, commitment_id: int, status: str
) -> Commitment | None:
    """Update a commitment's lifecycle status (e.g. dismissed, fulfilled)."""
    commitment = session.get(Commitment, commitment_id)
    if commitment is None:
        return None
    commitment.status = status
    session.flush()
    return commitment


def log_sync(
    session: Session,
    commitment_id: int,
    action: str,
    status: str = "success",
    error_message: str | None = None,
) -> SyncLog:
    """Record one calendar sync operation for auditing and retry."""
    entry = SyncLog(
        commitment_id=commitment_id,
        action=action,
        status=status,
        error_message=error_message,
    )
    session.add(entry)
    session.flush()
    return entry


def recent_sync_log(session: Session, limit: int = 20) -> list[SyncLog]:
    """Most recent sync operations, newest first."""
    stmt = (
        select(SyncLog)
        .order_by(SyncLog.synced_at.desc(), SyncLog.id.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def latest_sync_entries(session: Session) -> dict[int, SyncLog]:
    """The most recent sync-log row per commitment, keyed by commitment id."""
    stmt = select(SyncLog).order_by(SyncLog.synced_at.asc(), SyncLog.id.asc())
    latest: dict[int, SyncLog] = {}
    for entry in session.execute(stmt).scalars().all():
        latest[entry.commitment_id] = entry  # later rows overwrite earlier ones
    return latest


#: Actions belonging to the ``.ics`` channel — the one the retry queue governs.
#: Google Calendar writes are logged too (Phase 9) but under their own prefixed
#: actions, and are deliberately excluded from the retry tally: a spell offline
#: must not burn through SYNC_MAX_RETRIES and make the app give up publishing a
#: commitment to the local feed, which was succeeding the whole time.
ICS_SYNC_ACTIONS = frozenset({"created", "updated", "deleted"})


def failed_sync_attempts(session: Session) -> dict[int, int]:
    """Count of consecutive failed ``.ics`` sync attempts per commitment.

    Counts trailing failures only: a later success clears the tally, so a
    commitment that failed once and then synced is not treated as flaky.
    """
    stmt = select(SyncLog).order_by(SyncLog.synced_at.asc(), SyncLog.id.asc())
    attempts: dict[int, int] = {}
    for entry in session.execute(stmt).scalars().all():
        if entry.action not in ICS_SYNC_ACTIONS:
            continue
        if entry.status == "failed":
            attempts[entry.commitment_id] = attempts.get(entry.commitment_id, 0) + 1
        else:
            attempts.pop(entry.commitment_id, None)
    return attempts


def sync_retry_queue(
    session: Session, max_attempts: int | None = None
) -> list[Commitment]:
    """Commitments whose last sync failed and are still worth retrying.

    Gives the offline case a path back to consistency: a publish that failed
    because the disk was busy or the file was locked is picked up on the next
    run rather than being silently dropped.
    """
    cap = config.SYNC_MAX_RETRIES if max_attempts is None else max_attempts
    attempts = failed_sync_attempts(session)
    if not attempts:
        return []
    retryable = [cid for cid, count in attempts.items() if count <= cap]
    if not retryable:
        return []
    stmt = (
        select(Commitment)
        .where(Commitment.id.in_(retryable))
        .order_by(Commitment.deadline.asc())
    )
    return list(session.execute(stmt).scalars().all())


def list_commitments(
    session: Session,
    limit: int | None = None,
    commitment_type: str | None = None,
    status: str | None = None,
) -> list[Commitment]:
    """List commitments, newest deadline first (undated last)."""
    stmt = select(Commitment)
    if commitment_type:
        stmt = stmt.where(Commitment.type == commitment_type)
    if status:
        stmt = stmt.where(Commitment.status == status)
    stmt = stmt.order_by(Commitment.deadline.is_(None), Commitment.deadline.asc())
    if limit:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars().all())
