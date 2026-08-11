"""SQLite database access via SQLAlchemy.

Owns the engine/session lifecycle, schema creation, and small helpers used by the
collection layer. Schema: PROJECT_PLAN.md §10.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterable, Iterator

from sqlalchemy import ColumnElement, create_engine, event, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from src import config
from src.filtering.vip_filter import (
    SKIP,
    TIERS,
    normalize_match_type,
    normalize_match_value,
    normalize_tier,
)
from src.storage.models import Base, Commitment, RawEmail, SyncLog, VipContact

if TYPE_CHECKING:  # avoid a runtime import of higher layers from storage
    from src.collection.email_parser import ParsedEmail
    from src.extraction.schemas import ExtractedCommitment


engine = create_engine(config.DATABASE_URL, future=True)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):  # noqa: ANN001
    """Turn on foreign-key enforcement (SQLite defaults it off)."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


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
    ],
    # Phase 6.
    "raw_emails": [
        ("notification_seen", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
}

#: One-shot data fixes run in the same transaction as the ``ALTER TABLE`` that
#: first adds a column, as ``(table, column) -> SQL``. A fresh database never
#: reaches these: ``create_all`` makes the column, the ALTER is skipped, and so
#: is the backfill — which is correct, since there are no old rows to fix.
_COLUMN_BACKFILLS: dict[tuple[str, str], str] = {
    # Every email already in the database arrived before notifications existed,
    # so none of it is news. Without this, the first dashboard rerun after
    # upgrading would announce the user's entire VIP back-catalogue at once.
    ("raw_emails", "notification_seen"): "UPDATE raw_emails SET notification_seen = 1",
}


def _existing_columns(connection, table: str) -> set[str]:
    rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _migrate_added_columns() -> None:
    """Add any columns missing from an older database file.

    Additive only — no column is ever dropped, renamed, or retyped, so running
    this against a database holding real commitments cannot lose data. Each new
    column's backfill (if it has one) runs inside the same transaction as its
    ALTER, so the pair can never be left half-applied.
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
                backfill = _COLUMN_BACKFILLS.get((table, column))
                if backfill:
                    connection.exec_driver_sql(backfill)


def init_db() -> None:
    """Create the data directory and all tables if they don't already exist."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
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


# --- Notification policy (Phase 6) ----------------------------------------

#: Tiers whose arrival is worth telling the user about: every VIP tier except
#: SKIP. Derived rather than spelled out so it cannot drift from the filter's
#: own tier list.
NOTIFY_TIERS: tuple[str, ...] = tuple(tier for tier in TIERS if tier != SKIP)


def is_notifiable_tier(vip_tier: str | None) -> bool:
    """Whether email at this tier should raise a notification.

    Untagged email (``vip_tier IS NULL``) is excluded alongside SKIP: a sender
    the VIP filter has not classified is not yet *known* to be a VIP, and
    guessing would produce exactly the false alarms the tiers exist to prevent.
    """
    return vip_tier in NOTIFY_TIERS


def email_exists(session: Session, message_id: str) -> bool:
    """Return True if an email with this Message-ID is already stored."""
    stmt = select(RawEmail.id).where(RawEmail.message_id == message_id)
    return session.execute(stmt).first() is not None


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
        # Only VIP-tier arrivals are ever announced, so everything else is
        # stored already-seen rather than sitting unseen forever. That also
        # means promoting a SKIP sender to a VIP tier later cannot retroactively
        # announce their entire history — a notification is about arrival, and
        # those emails arrived while the user did not care about them.
        notification_seen=not is_notifiable_tier(vip_tier),
    )
    session.add(email)
    session.flush()  # assign the primary key within this transaction
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


def failed_sync_attempts(session: Session) -> dict[int, int]:
    """Count of consecutive failed sync attempts per commitment.

    Counts trailing failures only: a later success clears the tally, so a
    commitment that failed once and then synced is not treated as flaky.
    """
    stmt = select(SyncLog).order_by(SyncLog.synced_at.asc(), SyncLog.id.asc())
    attempts: dict[int, int] = {}
    for entry in session.execute(stmt).scalars().all():
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


# --- Notification state (Phase 6) -----------------------------------------

def _unseen_vip_filter() -> tuple[ColumnElement[bool], ...]:
    """The shared WHERE clause: a VIP email the user has not been told about."""
    return (
        RawEmail.notification_seen.is_(False),
        RawEmail.vip_tier.in_(NOTIFY_TIERS),
    )


def unseen_vip_emails(session: Session, limit: int | None = None) -> list[RawEmail]:
    """VIP-tier emails the user has not yet been shown a notification for.

    Newest first, because a toast that has to truncate should name the senders
    who just wrote rather than the oldest ones. Note this ignores ``processed``
    entirely: extraction is a separate concern and an email is news the moment
    it lands, not when the LLM gets round to it.
    """
    stmt = (
        select(RawEmail)
        .where(*_unseen_vip_filter())
        .order_by(RawEmail.received_at.desc(), RawEmail.id.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars().all())


def count_unseen_vip_emails(session: Session) -> int:
    """How many VIP emails are waiting to be announced (backs the sidebar badge).

    A count query rather than ``len(unseen_vip_emails(...))`` so the badge stays
    cheap on every Streamlit rerun even once the mailbox is large.
    """
    stmt = select(func.count()).select_from(RawEmail).where(*_unseen_vip_filter())
    return int(session.execute(stmt).scalar_one())


def mark_emails_seen(
    session: Session, email_ids: Iterable[int] | None = None
) -> int:
    """Mark notifications as seen. ``None`` means every unseen VIP email.

    Returns how many rows actually changed, which makes it idempotent in the way
    that matters: a second call (Streamlit reruns invite double submissions)
    finds nothing left unseen, rewrites nothing, and reports 0.
    """
    stmt = select(RawEmail).where(RawEmail.notification_seen.is_(False))
    if email_ids is None:
        stmt = stmt.where(RawEmail.vip_tier.in_(NOTIFY_TIERS))
    else:
        ids = list(email_ids)
        if not ids:
            return 0
        stmt = stmt.where(RawEmail.id.in_(ids))

    rows = list(session.execute(stmt).scalars().all())
    for email in rows:
        email.notification_seen = True
    session.flush()
    return len(rows)
