"""Decide what belongs on the calendar, and keep the feed matching the database.

Phase 5 of PROJECT_PLAN.md. Phase 4 published every dated commitment; this module
replaces that with the plan's tier policy:

==============  ==========================================================
CRITICAL        auto-syncs
IMPORTANT       auto-syncs, flagged for review
MONITOR         held back until the user approves it
SKIP            never syncs
==============  ==========================================================

Two rules cut across all tiers: ``question_pending`` commitments never reach the
calendar (a question is not an appointment), and a commitment with no deadline
cannot be an event.

:func:`decide` is a pure function of a single commitment, so the policy can be
tested and explained without a database. Every decision carries a human-readable
``reason``, which the review queue and sync report surface directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from src import config
from src.events.recorder import (
    CALENDAR_EVENT_CREATED,
    CALENDAR_EVENT_FAILED,
    CALENDAR_EVENT_REMOVED,
    CALENDAR_SYNCED,
    email_correlation,
    record_event,
)
from src.filtering.vip_filter import CRITICAL, IMPORTANT, MONITOR, SKIP
from src.storage import database
from src.storage.models import Commitment

log = logging.getLogger(__name__)

#: Commitment types that never belong on a calendar, whatever the tier.
NON_CALENDAR_TYPES = frozenset({"question_pending"})

#: Tiers that publish without the user having to do anything.
AUTO_SYNC_TIERS = frozenset({CRITICAL, IMPORTANT})

#: Tiers that publish but are flagged so the user can sanity-check them.
FLAGGED_TIERS = frozenset({IMPORTANT})

#: Tiers held in the review queue until explicitly approved.
APPROVAL_TIERS = frozenset({MONITOR})


@dataclass(frozen=True)
class SyncDecision:
    """Whether one commitment should be published, and why."""

    should_sync: bool
    reason: str
    #: True when the commitment syncs but the user should still eyeball it.
    needs_review: bool = False
    #: True when the user could approve this to make it sync.
    awaiting_approval: bool = False


def decide(commitment: Commitment) -> SyncDecision:
    """Apply the tier policy to a single commitment.

    Ordered most-disqualifying first, so the reason returned is the one that
    actually governs rather than whichever check happened to run first.
    """
    if commitment.type in NON_CALENDAR_TYPES:
        return SyncDecision(False, "questions do not go on the calendar")

    if commitment.deadline is None:
        return SyncDecision(False, "no deadline to put on the calendar")

    if commitment.status == "superseded":
        return SyncDecision(False, "replaced by a newer email")

    if commitment.status == "dismissed":
        return SyncDecision(False, "dismissed by the user")

    if commitment.status == "fulfilled":
        return SyncDecision(False, "already fulfilled")

    tier = (commitment.vip_tier or "").upper()

    # SKIP normally ends it: approving from the review queue is deliberately not
    # offered as a way past the skip list. Hand-picking one open email in the
    # Gmail panel is the single exception, because unknown senders are tiered
    # SKIP automatically — so without it, "Add to calendar" would silently do
    # nothing for anyone not already on the VIP list. Choosing an individual
    # email is a more specific statement than a rule about its sender.
    if tier == SKIP and not commitment.manually_added:
        return SyncDecision(False, "sender is on the skip list")

    if tier in AUTO_SYNC_TIERS:
        flagged = tier in FLAGGED_TIERS
        reason = (
            f"{tier} tier syncs automatically (flagged for review)"
            if flagged
            else f"{tier} tier syncs automatically"
        )
        return SyncDecision(True, reason, needs_review=flagged)

    # MONITOR, and anything untiered — both need a human to say yes. Treating an
    # unknown tier as "ask first" fails safe: a commitment is never published
    # off the back of a tier the policy does not recognise.
    if commitment.sync_approved:
        if tier == SKIP:
            return SyncDecision(True, "you added this from the Gmail panel")
        label = tier or "untiered"
        return SyncDecision(True, f"{label} commitment approved by you")

    if tier in APPROVAL_TIERS:
        return SyncDecision(
            False, f"{tier} tier waiting for your approval", awaiting_approval=True
        )

    return SyncDecision(
        False, "no VIP tier — waiting for your approval", awaiting_approval=True
    )


def calendar_commitments(session: Session) -> list[Commitment]:
    """The commitments that should currently be on the calendar.

    The single source of truth for feed contents: both the published ``.ics``
    file and the live server endpoint call this, so what a subscriber sees can
    never drift from what the database says.
    """
    return [c for c in database.calendar_candidates(session) if decide(c).should_sync]


def review_queue(session: Session) -> list[Commitment]:
    """Commitments the user could approve to put on the calendar."""
    return [
        c
        for c in database.commitments_awaiting_approval(session)
        if decide(c).awaiting_approval
    ]


@dataclass
class SyncReport:
    """What one sync cycle did — the summary the CLI and dashboard print."""

    published: int = 0
    created: int = 0
    updated: int = 0
    flagged: int = 0
    awaiting_approval: int = 0
    superseded: int = 0
    retried: int = 0
    failed: int = 0
    bytes_written: int = 0
    path: str = ""
    errors: list[str] = field(default_factory=list)
    # --- Google Calendar (Phase 9) ---
    #: None when not signed in, so "nothing to do" reads differently from "zero
    #: events synced".
    google_created: int | None = None
    google_updated: int | None = None
    google_removed: int | None = None
    google_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def google_connected(self) -> bool:
        return self.google_created is not None


#: Where an event was written, as it reads on the activity timeline.
_TARGET_LABELS = {"calendar_file": "your calendar file", "google": "Google Calendar"}


def _record_calendar_event(
    session: Session,
    event_type: str,
    commitment: Commitment,
    target: str,
    *,
    severity: str = "info",
    error: str | None = None,
) -> None:
    """One calendar outcome, filed under the email the commitment came from."""
    where = _TARGET_LABELS[target]
    if event_type == CALENDAR_EVENT_CREATED:
        message = f"Added to {where}: {commitment.subject}"
    elif event_type == CALENDAR_EVENT_REMOVED:
        message = f"Removed from {where}: {commitment.subject} ({commitment.status})"
    else:
        message = f"Could not add to {where}: {commitment.subject}. {error or ''}".strip()
    record_event(
        session,
        event_type,
        message,
        entity_type="commitment",
        entity_id=commitment.id,
        correlation_id=email_correlation(commitment.email_id),
        severity=severity,
        payload={
            "target": target,
            "deadline": commitment.deadline.isoformat() if commitment.deadline else None,
            **({"error": error} if error else {}),
        },
    )


def run_sync(session: Session, path=None) -> SyncReport:
    """Run one full sync cycle: resolve conflicts, then republish the calendar.

    Order matters. Conflicts are resolved first so a follow-up email has already
    superseded the commitment it replaces by the time the feed is built —
    otherwise the stale event would be published and only corrected next run.

    A publish failure is recorded against every commitment in the batch and left
    in the retry queue; the previously written ``.ics`` file stays in place, so
    subscribers keep seeing the last good feed rather than an empty calendar.
    """
    # Imported here: conflict_resolver imports this module for its decisions.
    from src.sync import conflict_resolver, ics_builder

    report = SyncReport()

    resolution = conflict_resolver.resolve_conflicts(session)
    report.superseded = resolution.superseded

    retrying = {c.id for c in database.sync_retry_queue(session)}

    selected = calendar_commitments(session)
    report.published = len(selected)
    report.flagged = sum(1 for c in selected if decide(c).needs_review)
    report.awaiting_approval = len(review_queue(session))
    report.retried = sum(1 for c in selected if c.id in retrying)

    target = path or config.ICS_PATH
    report.path = str(target)

    try:
        report.bytes_written = ics_builder.write_ics_file(selected, path=target)
    except OSError as exc:
        # Queue every commitment in this batch for retry rather than losing them.
        message = f"could not write {target}: {exc}"
        log.error("Calendar publish failed: %s", message)
        report.errors.append(message)
        report.failed = len(selected)
        for commitment in selected:
            database.log_sync(
                session,
                commitment.id,
                action="updated" if commitment.calendar_synced else "created",
                status="failed",
                error_message=str(exc),
            )
        record_event(
            session,
            CALENDAR_EVENT_FAILED,
            f"Could not write the calendar file; {len(selected)} events will be retried. {exc}",
            entity_type="calendar",
            severity="error",
            payload={"target": "calendar_file", "error": str(exc), "pending": len(selected)},
        )
        session.flush()
        return report

    for commitment in selected:
        action = "updated" if commitment.calendar_synced else "created"
        commitment.ics_uid = ics_builder.event_uid(commitment)
        commitment.calendar_synced = True
        database.log_sync(session, commitment.id, action=action, status="success")
        if action == "created":
            report.created += 1
            _record_calendar_event(
                session, CALENDAR_EVENT_CREATED, commitment, "calendar_file", severity="success"
            )
        else:
            report.updated += 1

    # Anything previously published but no longer eligible (dismissed, approval
    # revoked, superseded) must stop claiming to be on the calendar.
    published_ids = {c.id for c in selected}
    revoked = []
    for commitment in database.published_commitments(session):
        if commitment.id not in published_ids:
            commitment.calendar_synced = False
            revoked.append(commitment)
            database.log_sync(
                session, commitment.id, action="deleted", status="success"
            )
            _record_calendar_event(
                session, CALENDAR_EVENT_REMOVED, commitment, "calendar_file"
            )

    _push_to_google(session, report, selected, revoked)
    _record_summary(session, report)

    session.flush()
    return report


def _record_summary(session: Session, report: SyncReport) -> None:
    """One event per cycle: the heartbeat Flink measures sync throughput from."""
    parts = [f"{report.published} on the calendar ({report.created} new)"]
    if report.google_connected:
        parts.append(
            f"Google: {report.google_created} created, {report.google_updated} updated"
        )
    if report.superseded:
        parts.append(f"{report.superseded} superseded by follow-ups")
    problems = len(report.errors) + len(report.google_errors)
    record_event(
        session,
        CALENDAR_SYNCED,
        "Calendar synced: " + " · ".join(parts),
        entity_type="calendar",
        severity="warning" if problems else "success",
        payload={
            "published": report.published,
            "created": report.created,
            "updated": report.updated,
            "superseded": report.superseded,
            "awaiting_approval": report.awaiting_approval,
            "google_created": report.google_created,
            "google_updated": report.google_updated,
            "google_removed": report.google_removed,
            "errors": problems,
        },
    )


def _push_to_google(
    session: Session,
    report: SyncReport,
    selected: list[Commitment],
    revoked: list[Commitment],
) -> None:
    """Mirror the published set into Google Calendar, if signed in.

    Runs after the ``.ics`` file is already written, and never raises: the local
    feed is the guaranteed output, and losing the network must not turn a
    successful publish into a failed cycle. Google outcomes are logged under
    their own ``google_*`` actions, outside the retry queue's tally.
    """
    from src.sync import google_calendar

    if not google_calendar.is_available():
        return  # Not signed in: the counters stay None.

    report.google_created = 0
    report.google_updated = 0
    report.google_removed = 0

    try:
        service = google_calendar.build_service()
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        log.warning("Google Calendar unavailable this cycle: %s", exc)
        report.google_errors.append(str(exc))
        return

    try:
        pushed = google_calendar.push(session, selected, service=service)
    except Exception as exc:  # noqa: BLE001
        log.warning("Google Calendar push failed: %s", exc)
        report.google_errors.append(str(exc))
        return

    report.google_created = pushed.created
    report.google_updated = pushed.updated
    by_id = {c.id: c for c in selected}
    failed_ids = {cid for cid, _ in pushed.failed}
    for commitment in selected:
        if commitment.id not in failed_ids:
            database.log_sync(
                session, commitment.id, action="google_synced", status="success"
            )
    for commitment_id in pushed.created_ids:
        _record_calendar_event(
            session, CALENDAR_EVENT_CREATED, by_id[commitment_id], "google", severity="success"
        )
    for commitment_id, message in pushed.failed:
        report.google_errors.append(message)
        database.log_sync(
            session,
            commitment_id,
            action="google_synced",
            status="failed",
            error_message=message,
        )
        _record_calendar_event(
            session,
            CALENDAR_EVENT_FAILED,
            by_id[commitment_id],
            "google",
            severity="error",
            error=message,
        )

    try:
        report.google_removed = google_calendar.remove(
            session, revoked, service=service
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not remove revoked events from Google: %s", exc)
        report.google_errors.append(str(exc))
