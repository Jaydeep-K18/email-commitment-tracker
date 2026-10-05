"""The job state machine. Every transition is a database write.

::

    queued ──claim──> running ──complete──> succeeded
      ▲                  │
      │                  ├──fail (attempts left)──> retrying ──(due)──> claim again
      │                  ├──fail (none left / permanent)──> failed
      │                  └──lease expired (worker died)──> retrying | failed
      └────────────────── retry_job (manual) <──────────────────────────── failed

Three guarantees, and where each comes from:

**A job is enqueued once.** ``jobs.idempotency_key`` is UNIQUE and the insert
is ``ON CONFLICT DO NOTHING``, so enqueueing the same logical work twice —
"analyse email 42" from two fetches that both saw it — is a no-op enforced by
the database, not by hoping the callers coordinate.

**A job runs on one worker at a time.** :func:`claim` is a conditional
``UPDATE … WHERE status IN ('queued', 'retrying')``; whichever worker's update
lands first changes the status, and every other worker's matches zero rows. A
dispatcher delivering an id twice is therefore harmless.

**Nothing is lost when something dies.** A running job holds a lease
(``locked_until``). A worker that dies simply stops; its lease runs out, and
:func:`reap_expired` hands the job back. A Redis that loses its
data loses only ids; :func:`due_for_dispatch` finds every runnable row that is
not in Redis and the worker pushes it again.

What the queue cannot guarantee by itself is that a *handler* is safe to run
twice — a job can be interrupted after its work committed but before it was
marked complete. Every handler is written to be idempotent for that reason; see
:mod:`src.jobs.handlers`.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import event as sa_event
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from src import config
from src.events.recorder import (
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RETRIED,
    JOB_RETRYING,
    JOB_STARTED,
    record_event,
)
from src.jobs.backoff import backoff_seconds
from src.jobs.dispatch import RUNNABLE, get_dispatcher
from src.storage.models import Job, JobAttempt, utcnow_naive

log = logging.getLogger(__name__)

ACTIVE = ("queued", "retrying", "running")
TERMINAL = ("succeeded", "failed", "cancelled")


class PermanentJobError(Exception):
    """A failure that retrying cannot fix: bad input, revoked access, ..."""


def worker_name() -> str:
    """host:pid:thread — enough to tell, from the database, who held a job."""
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident() % 100000}"


@dataclass(frozen=True)
class Enqueued:
    job_id: int
    created: bool


# --- Dispatching after commit -----------------------------------------------

def _dispatch_after_commit(session: Session, job_id: int, run_at: datetime | None) -> None:
    """Hand the job to the dispatcher only once the row is committed.

    Pushing earlier would let a worker pop an id whose row it cannot yet see —
    or one belonging to a transaction about to roll back. If the push itself
    fails (Redis down), nothing is lost: the row exists, and the sweeper will
    dispatch it.
    """
    pending = session.info.setdefault("jobs_to_dispatch", [])
    pending.append((job_id, run_at))
    if session.info.get("jobs_dispatch_hooked"):
        return
    session.info["jobs_dispatch_hooked"] = True

    def after_commit(committed_session: Session) -> None:
        items = committed_session.info.pop("jobs_to_dispatch", [])
        for queued_id, due in items:
            try:
                get_dispatcher().push(queued_id, due)
            except Exception as exc:  # noqa: BLE001 - the sweeper recovers it
                log.warning("Could not dispatch job %s now (%s); it will be swept.", queued_id, exc)

    def after_rollback(rolled_back: Session) -> None:
        rolled_back.info.pop("jobs_to_dispatch", None)

    sa_event.listen(session, "after_commit", after_commit)
    sa_event.listen(session, "after_rollback", after_rollback)


# --- Enqueueing ---------------------------------------------------------------

def _insert_ignoring_conflict(session: Session, values: dict) -> bool:
    """INSERT … ON CONFLICT (idempotency_key) DO NOTHING. True if inserted."""
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    statement = insert(Job).values(**values).on_conflict_do_nothing(
        index_elements=["idempotency_key"]
    )
    return session.execute(statement).rowcount == 1


def enqueue(
    session: Session,
    job_type: str,
    payload: dict | None = None,
    *,
    key: str,
    max_attempts: int | None = None,
    priority: int = 0,
    run_at: datetime | None = None,
    correlation_id: str | None = None,
    source: str = "worker",
) -> Enqueued:
    """Add a job unless one with this idempotency key already exists."""
    now = utcnow_naive()
    created = _insert_ignoring_conflict(
        session,
        {
            "type": job_type,
            "idempotency_key": key,
            "payload": payload or {},
            "status": "queued",
            "priority": priority,
            "attempts": 0,
            "max_attempts": max_attempts or config.JOB_MAX_ATTEMPTS,
            "next_attempt_at": run_at,
            "correlation_id": correlation_id,
            "created_at": now,
            "updated_at": now,
        },
    )
    job_id = session.scalar(select(Job.id).where(Job.idempotency_key == key))
    if created:
        record_event(
            session,
            JOB_QUEUED,
            f"Queued: {describe(job_type, payload or {})}",
            entity_type="job",
            entity_id=job_id,
            correlation_id=correlation_id,
            payload={"job_type": job_type, "key": key},
            source=source,
        )
        _dispatch_after_commit(session, job_id, run_at)
    return Enqueued(job_id, created)


def enqueue_unless_active(
    session: Session,
    job_type: str,
    payload: dict | None = None,
    *,
    scope: str | None = None,
    **options,
) -> Enqueued:
    """Enqueue, unless the same work is already queued, retrying or running.

    For work worth doing again later but pointless to do twice *at once*:
    publishing the calendar, checking the mailbox, pushing one commitment's
    current content to Google. ``scope`` names "the same work"; the new job's
    key is the scope plus a unique suffix, so a later run is never blocked by an
    old finished one the way a fixed key would be.

    Two callers racing can both see nothing active and both enqueue. That is
    accepted rather than locked against: every job that uses this is safe to
    run twice, so the cost is one redundant run.
    """
    scope = scope or job_type
    # "_" and "%" are LIKE wildcards, and job types contain underscores.
    literal = scope.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    active = session.scalar(
        select(Job.id)
        .where(Job.idempotency_key.like(f"{literal}:%", escape="\\"), Job.status.in_(ACTIVE))
        .limit(1)
    )
    if active is not None:
        return Enqueued(active, False)
    return enqueue(session, job_type, payload, key=f"{scope}:{uuid.uuid4().hex[:12]}", **options)


# --- Running ------------------------------------------------------------------

def claim(session: Session, job_id: int, worker: str) -> tuple[Job, JobAttempt] | None:
    """Take a job for this worker, or None if someone else got it first."""
    now = utcnow_naive()
    claimed = session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status.in_(RUNNABLE),
            or_(Job.next_attempt_at.is_(None), Job.next_attempt_at <= now),
        )
        .values(
            status="running",
            attempts=Job.attempts + 1,
            locked_by=worker,
            locked_until=now + timedelta(seconds=config.JOB_LEASE_SECONDS),
            started_at=func.coalesce(Job.started_at, now),
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if claimed != 1:
        return None

    job = session.get(Job, job_id, populate_existing=True)
    attempt = JobAttempt(
        job_id=job.id, attempt=job.attempts, status="running", worker=worker, started_at=now
    )
    session.add(attempt)
    record_event(
        session,
        JOB_STARTED,
        f"Started: {describe(job.type, job.payload)}"
        + (f" (attempt {job.attempts} of {job.max_attempts})" if job.attempts > 1 else ""),
        entity_type="job",
        entity_id=job.id,
        correlation_id=job.correlation_id,
        payload={"job_type": job.type, "attempt": job.attempts, "worker": worker},
    )
    session.flush()
    return job, attempt


def complete(session: Session, job_id: int, attempt_id: int, result: dict | None, duration_ms: int) -> None:
    now = utcnow_naive()
    job = session.get(Job, job_id)
    attempt = session.get(JobAttempt, attempt_id)
    job.status = "succeeded"
    job.result = result or {}
    job.finished_at = now
    job.locked_by = job.locked_until = None
    attempt.status = "succeeded"
    attempt.finished_at = now
    attempt.duration_ms = duration_ms
    record_event(
        session,
        JOB_COMPLETED,
        f"Done: {describe(job.type, job.payload)}"
        + (f" — succeeded on attempt {job.attempts}" if job.attempts > 1 else ""),
        entity_type="job",
        entity_id=job.id,
        correlation_id=job.correlation_id,
        severity="success",
        payload={
            "job_type": job.type,
            "attempts": job.attempts,
            "duration_ms": duration_ms,
            "result": job.result,
        },
    )


def fail(
    session: Session, job_id: int, attempt_id: int | None, exc: BaseException, duration_ms: int | None
) -> datetime | None:
    """Record a failed attempt. Returns when to retry, or None if it is over."""
    now = utcnow_naive()
    job = session.get(Job, job_id)
    error = f"{type(exc).__name__}: {exc}"
    if attempt_id is not None:
        attempt = session.get(JobAttempt, attempt_id)
        attempt.status = "failed"
        attempt.error = error
        attempt.finished_at = now
        attempt.duration_ms = duration_ms

    job.last_error = error
    job.locked_by = job.locked_until = None
    permanent = isinstance(exc, PermanentJobError)

    if not permanent and job.attempts < job.max_attempts:
        delay = backoff_seconds(job.attempts)
        job.status = "retrying"
        job.next_attempt_at = now + timedelta(seconds=delay)
        record_event(
            session,
            JOB_RETRYING,
            f"Retrying in {_human_delay(delay)}: {describe(job.type, job.payload)} — {exc}",
            entity_type="job",
            entity_id=job.id,
            correlation_id=job.correlation_id,
            severity="warning",
            payload={
                "job_type": job.type,
                "attempt": job.attempts,
                "max_attempts": job.max_attempts,
                "delay_seconds": round(delay, 1),
                "error": error,
            },
        )
        return job.next_attempt_at

    job.status = "failed"
    job.finished_at = now
    record_event(
        session,
        JOB_FAILED,
        f"Failed: {describe(job.type, job.payload)} — {exc}",
        entity_type="job",
        entity_id=job.id,
        correlation_id=job.correlation_id,
        severity="error",
        payload={
            "job_type": job.type,
            "attempts": job.attempts,
            "error": error,
            "permanent": permanent,
        },
    )
    return None


# --- Recovery ------------------------------------------------------------------

def retry_job(session: Session, job_id: int, *, extra_attempts: int = 3, source: str = "worker") -> bool:
    """Give a failed or cancelled job another go. False if it is not in either state.

    The job keeps its row, its history and its idempotency key — a manual retry
    is the same unit of work continuing, not a new one — and gets a fresh
    budget of attempts on top of those already spent.
    """
    job = session.get(Job, job_id)
    if job is None or job.status not in ("failed", "cancelled"):
        return False
    job.status = "queued"
    job.next_attempt_at = None
    job.finished_at = None
    job.max_attempts = job.attempts + extra_attempts
    record_event(
        session,
        JOB_RETRIED,
        f"Retry requested: {describe(job.type, job.payload)}",
        entity_type="job",
        entity_id=job.id,
        correlation_id=job.correlation_id,
        payload={"job_type": job.type, "previous_attempts": job.attempts},
        source=source,
    )
    _dispatch_after_commit(session, job.id, None)
    return True


def reap_expired(session: Session) -> list[tuple[int, datetime | None]]:
    """Recover jobs whose worker stopped responding mid-run."""
    now = utcnow_naive()
    stale = session.scalars(
        select(Job).where(Job.status == "running", Job.locked_until < now)
    ).all()
    recovered = []
    for job in stale:
        latest = session.scalar(
            select(JobAttempt.id)
            .where(JobAttempt.job_id == job.id)
            .order_by(JobAttempt.attempt.desc())
            .limit(1)
        )
        lost = TimeoutError(f"worker {job.locked_by} stopped responding")
        recovered.append((job.id, fail(session, job.id, latest, lost, None)))
    session.flush()
    return recovered


def due_for_dispatch(session: Session, older_than_seconds: int = 30, limit: int = 200) -> list[int]:
    """Runnable jobs that are due — the candidates the sweeper re-pushes.

    Restricted to rows untouched for a little while, so a job enqueued a moment
    ago (whose own after-commit push is in flight) is not pushed twice.
    """
    now = utcnow_naive()
    return list(
        session.scalars(
            select(Job.id)
            .where(
                Job.status.in_(RUNNABLE),
                or_(Job.next_attempt_at.is_(None), Job.next_attempt_at <= now),
                Job.updated_at <= now - timedelta(seconds=older_than_seconds),
            )
            .order_by(Job.priority.desc(), Job.id)
            .limit(limit)
        )
    )


# --- Wording ---------------------------------------------------------------------

_DESCRIPTIONS = {
    "fetch_mailbox": "check the mailbox for new email",
    "process_email": "analyze email #{email_id}",
    "publish_calendar": "publish the calendar",
    "push_google_event": "send commitment #{commitment_id} to Google Calendar",
    "remove_google_event": "remove commitment #{commitment_id} from Google Calendar",
    "apply_vip_rules": "re-apply your contact rules to stored email",
    "enforce_retention": "apply your data-retention settings",
    "classify_emails": "re-classify selected emails",
}


def describe(job_type: str, payload: dict) -> str:
    template = _DESCRIPTIONS.get(job_type, job_type.replace("_", " "))
    try:
        return template.format(**payload)
    except (KeyError, IndexError):
        return template


def _human_delay(seconds: float) -> str:
    return f"{seconds:.0f}s" if seconds < 90 else f"{seconds / 60:.0f} min"
