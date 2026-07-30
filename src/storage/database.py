"""SQLite database access via SQLAlchemy.

Owns the engine/session lifecycle, schema creation, and small helpers used by the
collection layer. Schema: PROJECT_PLAN.md §10.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from src import config
from src.filtering.vip_filter import (
    normalize_match_type,
    normalize_match_value,
    normalize_tier,
)
from src.storage.models import Base, Commitment, RawEmail, VipContact

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


def init_db() -> None:
    """Create the data directory and all tables if they don't already exist."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


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
