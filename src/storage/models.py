"""SQLAlchemy ORM models.

Defines :class:`RawEmail` (Phase 1), :class:`VipContact` (Phase 2),
:class:`Commitment` (Phase 3) and :class:`SyncLog` (Phase 4) per
PROJECT_PLAN.md §10, and the v2 tables behind the full-stack app — events, jobs,
notifications, settings, auth and the smart inbox — further down.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
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

    # --- Smart inbox (v2) -------------------------------------------------
    # Mailbox state that lives only in this app. IMAP access stays read-only, so
    # archiving or deleting here changes what the tracker shows, never the real
    # mailbox. ``deleted_at`` is a soft delete; the retention job purges later.
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # important / action_required / meeting / update / low_priority. Set by the
    # rule classifier, refined after extraction, and fixed once the user picks
    # one themselves — ``category_source`` records which of those won, so a
    # later automatic pass can tell it must not overwrite a human choice.
    category: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    category_source: Mapped[str | None] = mapped_column(String, nullable=True)
    category_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Headers the parser used to discard. Bulk and invite flags drive the
    # classifier; In-Reply-To lets a sent reply be matched to the email it
    # answered, which is what response-time metrics are computed from.
    in_reply_to: Mapped[str | None] = mapped_column(String, nullable=True)
    cc: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_bulk: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_invite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_attachments: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    tags: Mapped[list["Tag"]] = relationship(
        secondary="email_tags", back_populates="emails"
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
    # pending / fulfilled / overdue / dismissed, plus ``superseded`` (Phase 5),
    # set when a follow-up email replaces this commitment.
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    # Calendar fields are populated in Phases 4-5.
    calendar_synced: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    ics_uid: Mapped[str | None] = mapped_column(String, nullable=True)
    # Phase 9. The id Google gave the event created for this commitment. Its
    # presence is what makes a second sync patch the existing event instead of
    # creating a duplicate, so it is the Google-side counterpart of ics_uid.
    gcal_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # v2. Hash of the event body last accepted by Google. A push job only runs
    # when the current body hashes differently, so an unchanged commitment
    # costs no API call — and the hash is part of the job's idempotency key, so
    # two pushes of the same content can never both be queued.
    gcal_synced_hash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Phase 10. True when the user picked this out of an open email in the Gmail
    # panel, rather than it being extracted in the background. Kept separate
    # from sync_approved because the two answer different questions: approval is
    # "yes, publish this one" from the review queue, whereas this records that
    # the user was looking at the email and chose it — a stronger statement,
    # and the only thing allowed to override a SKIP sender.
    manually_added: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Phase 5 additions, extending schema §10:
    # MONITOR-tier commitments stay off the calendar until the user approves
    # them; higher tiers are approved implicitly by the sync engine.
    sync_approved: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Set on the *newer* commitment when it replaces an earlier one, so the
    # supersede chain stays traceable back to each source email.
    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("commitments.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )

    source_email: Mapped["RawEmail"] = relationship(backref="commitments")
    supersedes: Mapped["Commitment | None"] = relationship(
        remote_side=[id], backref="superseded_by"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"Commitment(id={self.id!r}, type={self.type!r}, "
            f"subject={self.subject!r}, deadline={self.deadline!r})"
        )


class SyncLog(Base):
    """Audit trail of calendar sync operations (schema §10).

    Kept so a failed publish is visible and retryable rather than silent; Phase 5
    builds its retry logic on top of these rows.
    """

    __tablename__ = "sync_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    commitment_id: Mapped[int] = mapped_column(
        ForeignKey("commitments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # created / updated / deleted
    action: Mapped[str] = mapped_column(String, nullable=False)
    # success / failed / pending
    status: Mapped[str] = mapped_column(String, nullable=False, default="success")
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"SyncLog(id={self.id!r}, commitment_id={self.commitment_id!r}, "
            f"action={self.action!r}, status={self.status!r})"
        )


# ---------------------------------------------------------------------------
# v2 — the full-stack application
#
# Everything below is shared with the Node server, which reads and writes these
# tables directly. The schema itself is owned here and migrated by Alembic
# (``migrations/``); Node never alters it.
#
# Timestamps follow the convention the rest of this module already uses: naive
# UTC. The one exception anywhere in the schema is ``commitments.deadline``,
# which is the wall-clock time written in the email.
# ---------------------------------------------------------------------------

#: JSONB on Postgres (indexable, compact); plain JSON on SQLite for the tests.
JsonType = JSON().with_variant(JSONB(), "postgresql")

#: 64-bit keys for the append-heavy tables. SQLite only auto-increments a
#: column declared exactly ``INTEGER PRIMARY KEY``, so it gets that instead.
BigId = BigInteger().with_variant(Integer(), "sqlite")


class Tag(Base):
    """A user-defined label that can be attached to any number of emails."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # A palette key rather than a hex value, so the frontend's theme decides
    # what the colour actually looks like in light and dark mode.
    color: Mapped[str] = mapped_column(String(16), default="slate", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )

    emails: Mapped[list[RawEmail]] = relationship(
        secondary="email_tags", back_populates="tags"
    )


class EmailTag(Base):
    """Join table between emails and tags."""

    __tablename__ = "email_tags"

    email_id: Mapped[int] = mapped_column(
        ForeignKey("raw_emails.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )


class SavedView(Base):
    """A named inbox filter and sort the user can return to in one click."""

    __tablename__ = "saved_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    # Validated by the shared zod schema before it is stored, so the shape here
    # is whatever the inbox filter contract says it is.
    filters: Mapped[dict] = mapped_column(JsonType, default=dict, nullable=False)
    sort: Mapped[dict] = mapped_column(JsonType, default=dict, nullable=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


class SentMessage(Base):
    """Metadata of a message the user sent, for response-time metrics.

    Deliberately holds no body: reply time only needs to know *which* email was
    answered and *when*, and storing the user's outgoing mail would widen what
    this app keeps for no benefit.
    """

    __tablename__ = "sent_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    in_reply_to: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    recipient_email: Mapped[str | None] = mapped_column(String, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )


class Event(Base):
    """One thing that happened, recorded in the same transaction as the change.

    A single table serves three purposes that would otherwise be three copies of
    the same facts: the **audit trail** the activity timeline reads, the
    **system event log** the monitoring page reads, and the **transactional
    outbox** the relay publishes to Kafka from. Writing the event alongside the
    state change, rather than publishing to Kafka directly, is what stops the two
    from disagreeing — there is no moment where the database committed but the
    message was lost, or the reverse.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    # Dotted, e.g. "email.received", "calendar.event_created", "job.failed".
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Groups every event belonging to one email's journey ("email:42"), so the
    # timeline can show received → classified → analyzed → event created as one
    # story, even though the later steps are about commitments, not the email.
    correlation_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    # info / success / warning / error
    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    # A complete human sentence, so no consumer needs to know how to render each
    # event type in order to show something sensible.
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JsonType, default=dict, nullable=False)
    # worker / server / flink
    source: Mapped[str] = mapped_column(String(16), default="worker", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False, index=True
    )
    # NULL until the outbox relay has handed it to Kafka.
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_events_entity", "entity_type", "entity_id"),)


class Job(Base):
    """A unit of background work. The row, not Redis, is the job's truth.

    Redis only carries the id to a worker and holds delayed retries. If Redis
    were wiped, every job's status, attempt count and next run time would still
    be here, and the queue could be rebuilt from this table.
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    # fetch_mailbox / process_email / publish_calendar / push_google_event / ...
    type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    # The guarantee against duplicate work: enqueueing the same logical job
    # twice ("process_email:42") hits this constraint and becomes a no-op.
    idempotency_key: Mapped[str] = mapped_column(
        String(160), unique=True, nullable=False
    )
    payload: Mapped[dict] = mapped_column(JsonType, default=dict, nullable=False)
    # queued / running / retrying / succeeded / failed / cancelled
    status: Mapped[str] = mapped_column(
        String(16), default="queued", nullable=False, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Lease held by the worker running this job. A job whose lease has expired
    # belongs to a worker that died mid-run, and the reaper hands it back out.
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    attempt_history: Mapped[list["JobAttempt"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobAttempt.attempt",
    )


class JobAttempt(Base):
    """One execution of a job: the retry history, and the latency source."""

    __tablename__ = "job_attempts"

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    # running / succeeded / failed
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    job: Mapped[Job] = relationship(back_populates="attempt_history")

    __table_args__ = (UniqueConstraint("job_id", "attempt", name="uq_job_attempt"),)


class Notification(Base):
    """Something the user should be told about, derived from an event.

    ``UNIQUE(event_id, kind)`` makes derivation idempotent: Kafka delivers at
    least once, so the consumer may see an event twice, and the second insert
    simply does nothing.
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    # important_email / job_failed / job_retry_succeeded / calendar_failed / system
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    # A path inside the React app, e.g. "/inbox/42".
    link: Mapped[str | None] = mapped_column(String(200), nullable=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint("event_id", "kind", name="uq_notification_event_kind"),
    )


class Setting(Base):
    """User preferences, one row per section (profile, calendar, privacy, ...).

    JSON per section rather than a column per preference, because the set of
    preferences grows with every feature, and each section's shape is pinned
    down by the shared zod schema that validates it on the way in.
    """

    __tablename__ = "settings"

    section: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[dict] = mapped_column(JsonType, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


class OwnerAccount(Base):
    """The single account allowed to use the app.

    The CHECK constraint makes "single owner" a property of the database rather
    than of whichever code path happens to create the account.
    """

    __tablename__ = "owner_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # argon2id, written by the Node server. Never returned by any API.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )

    __table_args__ = (CheckConstraint("id = 1", name="ck_owner_account_single"),)


class AuthSession(Base):
    """A signed-in browser session.

    The primary key is a SHA-256 of the cookie's token, not the token itself, so
    reading this table does not hand anyone a usable session.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CalendarFlag(Base):
    """A possible duplicate or scheduling conflict, for the user to resolve.

    Flagged rather than fixed automatically: merging two commitments that were
    in fact different, or moving a meeting, is worse than asking.
    """

    __tablename__ = "calendar_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # duplicate / conflict
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    commitment_id: Mapped[int] = mapped_column(
        ForeignKey("commitments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    other_commitment_id: Mapped[int | None] = mapped_column(
        ForeignKey("commitments.id", ondelete="CASCADE"), nullable=True
    )
    # A Google Calendar event the commitment collides with, when it is not one
    # of ours.
    external_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Similarity scores, overlap window, suggested alternative slots.
    details: Mapped[dict] = mapped_column(JsonType, default=dict, nullable=False)
    # open / resolved / dismissed
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    # A UNIQUE over the nullable columns would not work, since two NULLs are
    # never equal, so detection writes a canonical key and constrains that.
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MetricSnapshot(Base):
    """One window of rolling metrics, from Flink or the SQL fallback."""

    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    # flink / fallback. Shown in the UI, so it is always clear which is live.
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # "1m" tumbling or "15m" sliding.
    window: Mapped[str] = mapped_column(String(8), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    metrics: Mapped[dict] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("source", "window", "window_start", name="uq_metric_window"),
    )


class ServiceHeartbeat(Base):
    """Last sign of life from each long-running process, for the health page.

    Kept in the database so health stays reportable when Redis is the thing
    that is down.
    """

    __tablename__ = "service_heartbeats"

    service: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    details: Mapped[dict] = mapped_column(JsonType, default=dict, nullable=False)
