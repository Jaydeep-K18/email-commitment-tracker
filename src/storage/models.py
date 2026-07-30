"""SQLAlchemy ORM models.

Defines :class:`RawEmail` (Phase 1), :class:`VipContact` (Phase 2) and
:class:`Commitment` (Phase 3) per PROJECT_PLAN.md §10. Phase 4 adds ``SyncLog``
on the shared :class:`Base`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow_naive() -> datetime:
    """Current UTC time as a naive datetime.

    SQLite (via SQLAlchemy's default ``DateTime``) stores naive datetimes, so we
    normalise to naive UTC everywhere to avoid mixing aware/naive values.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


class RawEmail(Base):
    """A fetched email, stored before and after processing (schema §10)."""

    __tablename__ = "raw_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The email's own Message-ID header — the dedup key.
    message_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String, nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String, nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Assigned by the VIP filter in Phase 2; NULL until then.
    vip_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    # Whether the extraction pipeline (Phase 3) has run on this email yet.
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"RawEmail(id={self.id!r}, sender_email={self.sender_email!r}, "
            f"subject={self.subject!r})"
        )


class VipContact(Base):
    """A user-defined rule mapping a sender to a priority tier (schema §10).

    Matching semantics live in :mod:`src.filtering.vip_filter`; this model only
    stores the rule.
    """

    __tablename__ = "vip_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Email address, name pattern, or domain — interpreted per ``match_type``.
    match_value: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # exact_email / name_pattern / domain
    match_type: Mapped[str] = mapped_column(String, nullable=False)
    # CRITICAL / IMPORTANT / MONITOR / SKIP
    tier: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )

    __table_args__ = (
        # One rule per (value, type); the tier can be updated in place.
        UniqueConstraint("match_value", "match_type", name="uq_vip_value_type"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"VipContact(id={self.id!r}, match_value={self.match_value!r}, "
            f"match_type={self.match_type!r}, tier={self.tier!r})"
        )


class Commitment(Base):
    """A commitment extracted from an email by the local LLM (schema §10).

    ``email_id`` links every row back to its source email, and
    ``evidence_quote`` stores the exact sentence that justified the extraction,
    so the user can always see *why* something was captured.
    """

    __tablename__ = "commitments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[int] = mapped_column(
        ForeignKey("raw_emails.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # deadline_on_you / deadline_from_others / question_pending / meeting
    type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    counterparty_name: Mapped[str | None] = mapped_column(String, nullable=True)
    counterparty_email: Mapped[str | None] = mapped_column(String, nullable=True)
    # incoming (they owe you) / outgoing (you owe them)
    direction: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_quote: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Inherited from the source email's VIP tier.
    vip_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    # pending / fulfilled / overdue / dismissed
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    # Calendar fields are populated in Phases 4-5.
    calendar_synced: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    ics_uid: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )

    source_email: Mapped["RawEmail"] = relationship(backref="commitments")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"Commitment(id={self.id!r}, type={self.type!r}, "
            f"subject={self.subject!r}, deadline={self.deadline!r})"
        )
