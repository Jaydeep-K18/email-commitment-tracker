"""What each job type does.

Every handler must be safe to run more than once for the same job. The queue
guarantees a job is never run by two workers *at the same time*, but a worker
can die after a handler's work committed and before the job was marked done;
the lease then expires and the job runs again. So each handler first checks
whether its work is already done, and the external calls it makes are
idempotent on Google's side too (see ``deterministic_event_id``).

The handlers reuse the pipeline's existing functions — ``fetch_and_store``,
``extract_from_email`` / ``store_analysis``, ``run_sync`` — so behaviour is
identical to the old in-process cycle. Only the orchestration changed: one
email, one push, one retry at a time, each visible and retryable on its own.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select

from src.events.recorder import (
    CALENDAR_EVENT_CREATED,
    CALENDAR_EVENT_FAILED,
    CALENDAR_EVENT_REMOVED,
    email_correlation,
)
from src.jobs.queue import PermanentJobError, enqueue, enqueue_unless_active
from src.storage.database import emails_awaiting_extraction, session_scope
from src.storage.models import Commitment, RawEmail

log = logging.getLogger(__name__)


def _holds_local_credentials() -> bool:
    """Whether this job's user owns the mail and Google sign-ins on this machine."""
    from src.storage.accounts import mailbox_owner_id
    from src.storage.tenancy import acting_as, current_user_id

    user_id = current_user_id()
    with acting_as(None), session_scope() as session:
        return user_id is not None and user_id == mailbox_owner_id(session)


def _require_local_credentials() -> None:
    if not _holds_local_credentials():
        raise PermanentJobError(
            "This server's mail and calendar sign-ins belong to its admin; "
            "this account has none of its own yet."
        )


def _default_fetch():
    from src.collection.email_fetcher import fetch_and_store

    _require_local_credentials()
    return fetch_and_store()


def _default_sync_sent() -> int:
    from src.collection.sent_fetcher import sync_sent_messages

    _require_local_credentials()
    return sync_sent_messages()


def _default_ollama():
    from src.extraction.ollama_client import OllamaClient

    return OllamaClient()


def _default_google_service():
    from src.sync import google_calendar

    _require_local_credentials()
    return google_calendar.build_service()


def _default_google_available() -> bool:
    from src.sync import google_calendar

    return _holds_local_credentials() and google_calendar.is_available()


@dataclass
class Services:
    """The outside world, injectable so the tests never reach a real one."""

    fetch: Callable[[], Any] = _default_fetch
    sync_sent: Callable[[], int] = _default_sync_sent
    ollama_client: Callable[[], Any] = _default_ollama
    google_service: Callable[[], Any] = _default_google_service
    google_available: Callable[[], bool] = _default_google_available


@dataclass
class JobContext:
    job_id: int
    attempt: int
    max_attempts: int
    services: Services = field(default_factory=Services)

    @property
    def is_last_attempt(self) -> bool:
        return self.attempt >= self.max_attempts


Handler = Callable[[dict, JobContext], dict]
HANDLERS: dict[str, Handler] = {}


def handler(job_type: str):
    def register(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn
    return register


def run_job(job_type: str, payload: dict, ctx: JobContext) -> dict:
    fn = HANDLERS.get(job_type)
    if fn is None:
        raise PermanentJobError(f"no handler for job type {job_type!r}")
    return fn(payload, ctx) or {}


# --- Mailbox ----------------------------------------------------------------

#: Upper bound on analysis jobs queued per fetch, so a first run against a
#: large backlog does not bury every other job.
BACKLOG_BATCH = 200


@handler("fetch_mailbox")
def fetch_mailbox(payload: dict, ctx: JobContext) -> dict:
    """Store new mail, then queue one analysis job per email awaiting one.

    Queues from the whole backlog of unanalyzed email, not just this fetch's
    arrivals, so mail left over from before the job system existed — or from a
    fetch whose follow-up was lost — is picked up too. The per-email key makes
    that safe: an email that already has a job is not queued again.
    """
    from src.storage.user_settings import apply_fetch_settings

    from src.auth.google_auth import needs_sign_in

    with session_scope() as session:
        apply_fetch_settings(session)
    try:
        fetched = ctx.services.fetch()
    except Exception as exc:
        if needs_sign_in(exc):
            raise PermanentJobError(str(exc)) from exc   # retrying cannot help
        raise
    with session_scope() as session:
        backlog = emails_awaiting_extraction(session, limit=BACKLOG_BATCH)
        queued = sum(
            enqueue(
                session,
                "process_email",
                {"email_id": email.id},
                key=f"process_email:{email.id}",
                correlation_id=email_correlation(email.id),
            ).created
            for email in backlog
        )
    sent = ctx.services.sync_sent()
    return {
        "fetched": fetched.fetched,
        "new": fetched.new,
        "queued_for_analysis": queued,
        "sent_headers": sent,
    }


@handler("process_email")
def process_email(payload: dict, ctx: JobContext) -> dict:
    """Analyse one email with the local model.

    The model call runs with no transaction open: it can take minutes on a CPU,
    and holding a Postgres transaction that long blocks vacuum and pins
    resources for nothing. The result is stored in a fresh transaction that
    re-checks the email was not analysed in the meantime.
    """
    from src.extraction.pipeline import (
        extract_from_email,
        record_extraction_failure,
        store_analysis,
    )

    email_id = int(payload["email_id"])
    with session_scope() as session:
        email = session.get(RawEmail, email_id)
        if email is None:
            return {"skipped": "the email no longer exists"}
        if email.processed:
            return {"skipped": "already analyzed"}
        session.expunge(email)

    client = ctx.services.ollama_client()
    client.ensure_ready()   # Ollama down -> raises -> backoff and retry

    try:
        outcome = extract_from_email(client, email)
    except Exception as exc:
        record_extraction_failure(email_id, exc)
        raise

    with session_scope() as session:
        fresh = session.get(RawEmail, email_id)
        if fresh is None or fresh.processed:
            return {"skipped": "analyzed by another run meanwhile"}
        store_analysis(session, fresh, outcome)
        if outcome.commitments:
            enqueue_unless_active(session, "publish_calendar")

    return {
        "commitments": len(outcome.commitments),
        "retried": outcome.retried,
        "discarded": outcome.discarded,
        "duration_ms": round(outcome.duration_seconds * 1000),
    }


# --- Calendar ---------------------------------------------------------------

@handler("publish_calendar")
def publish_calendar(payload: dict, ctx: JobContext) -> dict:
    """Rewrite the .ics file, then queue a Google push for what changed.

    Google is not called here. Each changed commitment gets its own push job,
    keyed by the hash of the content being pushed, so one rejected event is
    retried on its own schedule without holding up the others, and an
    unchanged commitment costs no API call at all.
    """
    from src.sync import google_calendar
    from src.sync.sync_engine import run_sync

    with session_scope() as session:
        report = run_sync(session, push_google=False)
        pushes = removals = 0
        if ctx.services.google_available():
            published = session.scalars(
                select(Commitment).where(Commitment.id.in_(report.selected_ids))
            )
            for commitment in published:
                content = google_calendar.event_content_hash(commitment)
                if content == commitment.gcal_synced_hash:
                    continue
                pushes += enqueue_unless_active(
                    session,
                    "push_google_event",
                    {"commitment_id": commitment.id, "content_hash": content},
                    scope=f"gcal_push:{commitment.id}:{content}",
                    correlation_id=email_correlation(commitment.email_id),
                ).created

            revoked = session.scalars(
                select(Commitment).where(
                    Commitment.id.in_(report.revoked_ids),
                    Commitment.gcal_event_id.is_not(None),
                )
            )
            for commitment in revoked:
                removals += enqueue_unless_active(
                    session,
                    "remove_google_event",
                    {"commitment_id": commitment.id, "event_id": commitment.gcal_event_id},
                    scope=f"gcal_remove:{commitment.id}:{commitment.gcal_event_id}",
                    correlation_id=email_correlation(commitment.email_id),
                ).created

        # What is on the calendar may have changed; look again for clashes.
        enqueue_unless_active(session, "scan_calendar")

    # Raised after the transaction above committed, so the failure's sync-log
    # rows and event survive; the job then retries with backoff.
    if report.errors:
        raise RuntimeError("; ".join(report.errors))

    return {
        "published": report.published,
        "created": report.created,
        "google_pushes_queued": pushes,
        "google_removals_queued": removals,
    }


@handler("scan_calendar")
def scan_calendar(payload: dict, ctx: JobContext) -> dict:
    """Flag possible duplicates and clashes among upcoming events.

    The user's own Google events are read when Google Calendar is connected.
    If it cannot be read, the commitments are still checked against each
    other, and flags about Google events are left as they were.
    """
    from datetime import datetime, timedelta

    from src.storage import user_settings
    from src.sync import calendar_intel, google_calendar

    now = datetime.now()   # wall clock, as deadlines are stored
    external: list[dict] = []
    google = "not connected"
    if ctx.services.google_available():
        try:
            external = google_calendar.list_user_events(
                ctx.services.google_service(),
                datetime.combine(now.date(), datetime.min.time()),
                now + timedelta(days=calendar_intel.HORIZON_DAYS),
            )
            google = "checked"
        except Exception as exc:  # noqa: BLE001 - any failure: check without it
            log.warning("Could not read Google Calendar for the clash check: %s", exc)
            google = f"unavailable: {exc}"

    with session_scope() as session:
        hours = calendar_intel.WorkingHours.from_settings(user_settings.section(session, "calendar"))
        result = calendar_intel.scan(session, external, hours, now, external_checked=google == "checked")
    return {"found": result.found, "new": result.new, "cleared": result.cleared, "google": google}


def _google_failure(exc: BaseException) -> BaseException:
    from src.sync import google_calendar

    from src.auth.google_auth import needs_sign_in

    if google_calendar.is_retryable(exc):
        return exc
    if needs_sign_in(exc):
        return PermanentJobError(str(exc))   # already says what to do
    return PermanentJobError(f"Google rejected it: {exc}")


@handler("push_google_event")
def push_google_event(payload: dict, ctx: JobContext) -> dict:
    """Create or update one commitment's Google event, if it still needs it."""
    from src.sync import google_calendar
    from src.sync.sync_engine import decide, record_calendar_event

    if not ctx.services.google_available():
        return {"skipped": "Google Calendar is not connected"}

    commitment_id = int(payload["commitment_id"])
    expected = payload["content_hash"]
    with session_scope() as session:
        commitment = session.get(Commitment, commitment_id)
        if commitment is None or not decide(commitment).should_sync:
            return {"skipped": "no longer belongs on the calendar"}
        current = google_calendar.event_content_hash(commitment)
        if current != expected:
            # The commitment changed after this job was queued; the job for its
            # new content does the push, so this one would only be overwritten.
            return {"skipped": "superseded by newer content"}
        if commitment.gcal_synced_hash == current:
            return {"skipped": "Google already has this version"}

        try:
            outcome = google_calendar.push_commitment(
                session, commitment, service=ctx.services.google_service()
            )
        except Exception as exc:
            failure = _google_failure(exc)
            if isinstance(failure, PermanentJobError) or ctx.is_last_attempt:
                _record_push_failure(commitment_id, str(exc))
            raise failure from exc

        if outcome == "created":
            record_calendar_event(
                session, CALENDAR_EVENT_CREATED, commitment, "google", severity="success"
            )
        return {"outcome": outcome, "event_id": commitment.gcal_event_id}


def _record_push_failure(commitment_id: int, error: str) -> None:
    """In a transaction of its own: the push's transaction is rolling back."""
    from src.sync.sync_engine import record_calendar_event

    with session_scope() as session:
        commitment = session.get(Commitment, commitment_id)
        if commitment is not None:
            record_calendar_event(
                session, CALENDAR_EVENT_FAILED, commitment, "google",
                severity="error", error=error,
            )


@handler("remove_google_event")
def remove_google_event(payload: dict, ctx: JobContext) -> dict:
    """Delete an event for a commitment that left the calendar."""
    from googleapiclient.errors import HttpError

    from src import config
    from src.sync.sync_engine import decide, record_calendar_event

    if not ctx.services.google_available():
        return {"skipped": "Google Calendar is not connected"}

    commitment_id = int(payload["commitment_id"])
    event_id = payload["event_id"]
    with session_scope() as session:
        commitment = session.get(Commitment, commitment_id)
        if commitment is not None and decide(commitment).should_sync:
            return {"skipped": "back on the calendar since this was queued"}

        try:
            ctx.services.google_service().events().delete(
                calendarId=config.GOOGLE_CALENDAR_ID, eventId=event_id
            ).execute()
        except HttpError as exc:
            if exc.resp.status not in (404, 410):   # already gone is the goal
                raise _google_failure(exc) from exc

        if commitment is not None and commitment.gcal_event_id == event_id:
            commitment.gcal_event_id = None
            commitment.gcal_synced_hash = None
            record_calendar_event(session, CALENDAR_EVENT_REMOVED, commitment, "google")
        return {"removed": event_id}


# --- Contacts and privacy -----------------------------------------------------

@handler("apply_vip_rules")
def apply_vip_rules(payload: dict, ctx: JobContext) -> dict:
    """Re-evaluate every stored email after the VIP rules changed.

    Three things depend on a sender's tier and all three are refreshed: the
    email's own tier, its inbox category, and the tier its commitments carry —
    which is what the calendar policy reads, so demoting a sender takes their
    commitments off auto-publish. A category the user chose by hand is kept.
    """
    from sqlalchemy import or_, update

    from src.classification.service import apply_classification
    from src.filtering.vip_filter import apply_tiers_to_stored_emails

    with session_scope() as session:
        counts = apply_tiers_to_stored_emails(session, retag_all=True)
        tier_of_email = (
            select(RawEmail.vip_tier).where(RawEmail.id == Commitment.email_id).scalar_subquery()
        )
        session.execute(
            update(Commitment).values(vip_tier=tier_of_email).execution_options(
                synchronize_session=False
            )
        )
        reclassified = sum(
            apply_classification(session, email)
            for email in session.scalars(
                select(RawEmail).where(
                    or_(RawEmail.category_source.is_(None), RawEmail.category_source != "user")
                )
            )
        )
        queued = sum(
            enqueue(
                session,
                "process_email",
                {"email_id": email.id},
                key=f"process_email:{email.id}",
                correlation_id=email_correlation(email.id),
            ).created
            for email in emails_awaiting_extraction(session, limit=BACKLOG_BATCH)
        )
        enqueue_unless_active(session, "publish_calendar")
    return {"retagged": counts["total"], "reclassified": reclassified, "queued_for_analysis": queued}


@handler("enforce_retention")
def enforce_retention(payload: dict, ctx: JobContext) -> dict:
    """Apply the user's privacy settings: delete old mail, blank analysed bodies.

    An email is kept past its retention date while it still has a pending
    commitment with a deadline ahead of it — deleting it would silently remove
    an upcoming event from the user's calendar.
    """
    from datetime import timedelta

    from sqlalchemy import delete, update

    from src.events.recorder import DATA_PURGED, record_event
    from src.storage import user_settings
    from src.storage.models import Event, utcnow_naive

    with session_scope() as session:
        privacy = user_settings.section(session, "privacy")
        days = int(privacy.get("retentionDays", 0) or 0)
        keep_bodies = bool(privacy.get("keepEmailBodies", True))
        now = utcnow_naive()
        deleted_emails = deleted_events = blanked = 0

        if days > 0:
            cutoff = now - timedelta(days=days)
            still_needed = select(Commitment.email_id).where(
                Commitment.status == "pending", Commitment.deadline >= now
            )
            deleted_emails = session.execute(
                delete(RawEmail).where(
                    RawEmail.received_at < cutoff, RawEmail.id.not_in(still_needed)
                )
            ).rowcount
            deleted_events = session.execute(
                delete(Event).where(Event.created_at < cutoff)
            ).rowcount

        if not keep_bodies:
            blanked = session.execute(
                update(RawEmail)
                .where(RawEmail.processed.is_(True), RawEmail.body_text.is_not(None))
                .values(body_text=None)
            ).rowcount

        if deleted_emails or deleted_events or blanked:
            record_event(
                session,
                DATA_PURGED,
                f"Retention: removed {deleted_emails} email(s) and {deleted_events} "
                f"activity record(s); cleared {blanked} analysed email bod{'y' if blanked == 1 else 'ies'}",
                entity_type="privacy",
                payload={
                    "retention_days": days,
                    "emails": deleted_emails,
                    "events": deleted_events,
                    "bodies": blanked,
                },
            )
    return {"emails": deleted_emails, "events": deleted_events, "bodies": blanked}


@handler("classify_emails")
def classify_emails(payload: dict, ctx: JobContext) -> dict:
    """Re-run the classifier on specific emails — after a user hands a category
    back to automatic, for one. Skips any whose category the user still owns."""
    from src.classification.service import apply_classification

    email_ids = [int(value) for value in payload.get("email_ids", [])][:1000]
    with session_scope() as session:
        changed = sum(
            apply_classification(session, email)
            for email in session.scalars(select(RawEmail).where(RawEmail.id.in_(email_ids)))
        )
    return {"requested": len(email_ids), "changed": changed}
