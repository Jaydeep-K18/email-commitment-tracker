"""Background loop that keeps the tracker up to date without being asked.

Phase 6 of PROJECT_PLAN.md. One cycle runs the whole pipeline end to end:

    fetch new email  ->  extract commitments  ->  republish the calendar

Run it standalone::

    python -m src.collection.scheduler

or let the dashboard start it (see ``dashboard/app.py``).

Each stage is isolated: if the inbox is unreachable the cycle still re-syncs
what is already in the database, and if the local LLM is down the fetched email
simply stays queued for the next run. A cycle therefore never leaves the system
in a worse state than it found it.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime

from src import config
from src.storage.database import init_db, session_scope

log = logging.getLogger(__name__)


@dataclass
class CycleResult:
    """What one fetch/extract/sync cycle achieved."""

    started_at: datetime = field(default_factory=datetime.now)
    emails_fetched: int = 0
    emails_new: int = 0
    commitments_extracted: int = 0
    events_published: int = 0
    superseded: int = 0
    #: ``(stage, message)`` for any stage that failed. A cycle can partly
    #: succeed, so this is a list rather than a single error.
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = [
            f"{self.emails_new} new email(s)",
            f"{self.commitments_extracted} commitment(s)",
            f"{self.events_published} calendar event(s)",
        ]
        if self.superseded:
            parts.append(f"{self.superseded} superseded")
        line = ", ".join(parts)
        if self.errors:
            failed = ", ".join(stage for stage, _ in self.errors)
            line += f"  [failed: {failed}]"
        return line


def run_cycle(
    *, fetch: bool = True, extract: bool | None = None, sync: bool = True
) -> CycleResult:
    """Run one full pipeline cycle, tolerating failure in any single stage."""
    if extract is None:
        extract = config.SCHEDULER_RUN_EXTRACTION

    result = CycleResult()
    init_db()

    if fetch:
        try:
            # Imported lazily so a scheduler import does not pull in imaplib,
            # Ollama, and the sync engine before they are needed.
            from src.collection.email_fetcher import fetch_and_store

            fetched = fetch_and_store()
            result.emails_fetched = fetched.fetched
            result.emails_new = fetched.new
            log.info("Fetched %d email(s), %d new.", fetched.fetched, fetched.new)
        except Exception as exc:  # noqa: BLE001 - a cycle must survive any stage
            log.error("Fetch stage failed: %s", exc)
            result.errors.append(("fetch", str(exc)))

    if extract:
        try:
            from src.extraction.pipeline import process_pending_emails

            stats = process_pending_emails(limit=config.SCHEDULER_EXTRACTION_LIMIT)
            result.commitments_extracted = stats.commitments_stored
            log.info("Extracted %d commitment(s).", stats.commitments_stored)
        except Exception as exc:  # noqa: BLE001
            log.error("Extraction stage failed: %s", exc)
            result.errors.append(("extract", str(exc)))

    if sync:
        try:
            from src.sync.sync_engine import run_sync

            with session_scope() as session:
                report = run_sync(session)
            result.events_published = report.published
            result.superseded = report.superseded
            for message in report.errors:
                result.errors.append(("sync", message))
            log.info("Published %d calendar event(s).", report.published)
        except Exception as exc:  # noqa: BLE001
            log.error("Sync stage failed: %s", exc)
            result.errors.append(("sync", str(exc)))

    log.info("Cycle complete: %s", result.summary())
    return result


class SchedulerHandle:
    """Owns the APScheduler instance and the most recent cycle result.

    The dashboard holds one of these so it can show when the last run happened
    and what it did, without the scheduler needing to know the UI exists.
    """

    def __init__(self, interval_minutes: int | None = None) -> None:
        self.interval_minutes = (
            interval_minutes or config.SCHEDULER_INTERVAL_MINUTES
        )
        self._scheduler = None
        self._lock = threading.Lock()
        self.last_result: CycleResult | None = None
        self.last_run_at: datetime | None = None
        self.history: list[CycleResult] = []

    # -- job ---------------------------------------------------------------

    def _job(self) -> None:
        result = run_cycle()
        with self._lock:
            self.last_result = result
            self.last_run_at = result.started_at
            self.history.append(result)
            del self.history[:-20]  # keep the tail bounded

    def run_now(self) -> CycleResult:
        """Run a cycle immediately on the calling thread."""
        self._job()
        return self.last_result  # type: ignore[return-value]

    # -- lifecycle ---------------------------------------------------------

    def start(self, run_immediately: bool = False) -> None:
        """Start the background schedule (idempotent)."""
        if self.running:
            return

        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            self._job,
            trigger="interval",
            minutes=self.interval_minutes,
            id="tracker_cycle",
            name="fetch -> extract -> sync",
            # A cycle can outlast its interval (LLM extraction is slow), so
            # never let a second one start on top of the first, and collapse
            # any runs missed while it was busy into a single catch-up.
            max_instances=1,
            coalesce=True,
            misfire_grace_time=None,
        )
        scheduler.start()
        self._scheduler = scheduler
        log.info("Scheduler started; every %d minute(s).", self.interval_minutes)

        if run_immediately:
            threading.Thread(target=self._job, daemon=True).start()

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            log.info("Scheduler stopped.")

    @property
    def running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    @property
    def next_run_at(self) -> datetime | None:
        if not self.running:
            return None
        job = self._scheduler.get_job("tracker_cycle")  # type: ignore[union-attr]
        return getattr(job, "next_run_time", None)


def _main() -> int:
    """Run the scheduler in the foreground until interrupted."""
    import time

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    handle = SchedulerHandle()
    print("Email Commitment Tracker - scheduler")
    print(f"  Cycle: fetch -> extract -> sync, every {handle.interval_minutes} min")
    print("  Press Ctrl+C to stop.\n")
    handle.start(run_immediately=True)
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        print("\nStopping...")
        handle.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
