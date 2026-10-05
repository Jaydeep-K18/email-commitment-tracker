"""The background worker: runs jobs, retries failures, recovers from crashes.

    python -m src.jobs.worker                 run until stopped (Ctrl+C)
    python -m src.jobs.worker --once          drain what is queued, then exit
    python -m src.jobs.worker --concurrency 2

One process does three things:

* **Run jobs.** ``WORKER_CONCURRENCY`` threads each take an id from the
  dispatcher, claim it against the database, run its handler, and record the
  outcome.
* **Maintain the queue.** Every few seconds: move retries whose backoff has
  elapsed onto the ready list, recover jobs whose worker died (expired lease),
  and re-dispatch any runnable job Redis does not know about.
* **Schedule.** Every ``FETCH_INTERVAL_MINUTES`` it queues a mailbox check and
  a calendar publish. It *queues* them — it never runs them inline — so a
  scheduled run is retried, reported and visible exactly like any other job.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from sqlalchemy import select

from src import config
from src.events.recorder import SYSTEM_WORKER_STARTED, SYSTEM_WORKER_STOPPED, record_event
from src.jobs import queue
from src.jobs.dispatch import Dispatcher, RedisDispatcher, get_dispatcher
from src.jobs.handlers import JobContext, Services, run_job
from src.storage import user_settings
from src.storage.database import init_db, session_scope
from src.storage.models import Job, ServiceHeartbeat, utcnow_naive

log = logging.getLogger(__name__)

#: Seconds between maintenance passes.
MAINTENANCE_INTERVAL = 5.0
#: Retention is enforced once a day; it only ever deletes days-old data.
RETENTION_INTERVAL = 24 * 60 * 60


class Worker:
    def __init__(
        self,
        dispatcher: Dispatcher | None = None,
        services: Services | None = None,
        concurrency: int | None = None,
        schedule: bool = True,
    ) -> None:
        self.dispatcher = dispatcher or get_dispatcher()
        self.services = services or Services()
        self.concurrency = max(1, concurrency or config.WORKER_CONCURRENCY)
        self.schedule = schedule
        self.name = queue.worker_name()
        self.stop_event = threading.Event()
        self.jobs_run = 0
        self._next_schedule = 0.0   # monotonic; 0 means "due now"
        self._next_retention = 0.0
        self._counter_lock = threading.Lock()

    # --- One job ----------------------------------------------------------

    def run_one(self, timeout: float = 1.0) -> bool:
        """Take one job and run it. False if nothing was claimable in ``timeout``."""
        job_id = self.dispatcher.pop(timeout)
        if job_id is None:
            return False
        try:
            return self.process(job_id)
        finally:
            self.dispatcher.ack(job_id)

    def process(self, job_id: int) -> bool:
        with session_scope() as session:
            claimed = queue.claim(session, job_id, self.name)
            if claimed is None:
                return False   # another worker has it, or it is no longer runnable
            job, attempt = claimed
            job_type, payload = job.type, dict(job.payload or {})
            attempt_id, attempt_no, max_attempts = attempt.id, job.attempts, job.max_attempts

        ctx = JobContext(job_id, attempt_no, max_attempts, self.services)
        started = time.monotonic()
        try:
            result = run_job(job_type, payload, ctx)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a retry or a failed job
            elapsed = _ms_since(started)
            log.warning("Job %s (%s) attempt %s failed: %s", job_id, job_type, attempt_no, exc)
            with session_scope() as session:
                retry_at = queue.fail(session, job_id, attempt_id, exc, elapsed)
            if retry_at is not None:
                self.dispatcher.push(job_id, retry_at)
        else:
            with session_scope() as session:
                queue.complete(session, job_id, attempt_id, result, _ms_since(started))
            log.info("Job %s (%s) done in %sms", job_id, job_type, _ms_since(started))

        with self._counter_lock:
            self.jobs_run += 1
        return True

    # --- Upkeep -----------------------------------------------------------

    def maintain(self) -> None:
        """One maintenance pass. Each step is independent and survives failure."""
        for step in (self._promote, self._reap, self._sweep, self._heartbeat, self._schedule):
            try:
                step()
            except Exception as exc:  # noqa: BLE001 - upkeep must never kill the worker
                log.warning("Maintenance step %s failed: %s", step.__name__, exc)

    def _promote(self) -> None:
        self.dispatcher.promote_due()

    def _reap(self) -> None:
        with session_scope() as session:
            recovered = queue.reap_expired(session)
        for job_id, retry_at in recovered:
            log.warning("Recovered job %s from a worker that stopped responding.", job_id)
            if retry_at is not None:
                self.dispatcher.push(job_id, retry_at)

    def _sweep(self) -> None:
        """Re-dispatch runnable jobs the dispatcher has lost track of."""
        with session_scope() as session:
            due = queue.due_for_dispatch(session)
            running = set(session.scalars(select(Job.id).where(Job.status == "running")))
        for job_id in due:
            if not self.dispatcher.contains(job_id):
                self.dispatcher.push(job_id)
        if isinstance(self.dispatcher, RedisDispatcher):
            self.dispatcher.prune_processing(running)

    def _heartbeat(self) -> None:
        with session_scope() as session:
            session.merge(
                ServiceHeartbeat(
                    service="worker",
                    last_seen_at=utcnow_naive(),
                    details={
                        "worker": self.name,
                        "dispatcher": self.dispatcher.name,
                        "concurrency": self.concurrency,
                        "jobs_run": self.jobs_run,
                        "queue": self.dispatcher.depth(),
                    },
                )
            )

    def _schedule(self) -> None:
        if not self.schedule:
            return
        now = time.monotonic()
        if now >= self._next_schedule:
            with session_scope() as session:
                queue.enqueue_unless_active(session, "fetch_mailbox")
                queue.enqueue_unless_active(session, "publish_calendar")
                # Read every time, so a change on the settings page applies from
                # the next check rather than after a restart.
                interval = user_settings.fetch_interval_minutes(session)
            self._next_schedule = now + interval * 60
        if now >= self._next_retention:
            with session_scope() as session:
                queue.enqueue_unless_active(session, "enforce_retention")
            self._next_retention = now + RETENTION_INTERVAL

    # --- Lifecycle --------------------------------------------------------

    def run_until_idle(self, idle_timeout: float = 0.2, max_jobs: int = 10_000) -> int:
        """Run jobs until none is claimable. Single-threaded; for tests and --once."""
        ran = 0
        while ran < max_jobs:
            self.maintain()
            if not self.run_one(timeout=idle_timeout):
                # A retry may have just become due; one more maintenance pass
                # decides whether we are really idle.
                self.maintain()
                if not self.run_one(timeout=idle_timeout):
                    break
            ran += 1
        return ran

    def run_forever(self) -> None:
        self._record_lifecycle(SYSTEM_WORKER_STARTED, "Worker started")
        threads = [
            threading.Thread(target=self._job_loop, name=f"job-{i}", daemon=True)
            for i in range(self.concurrency)
        ]
        for thread in threads:
            thread.start()
        try:
            while not self.stop_event.is_set():
                self.maintain()
                self.stop_event.wait(MAINTENANCE_INTERVAL)
        finally:
            self.stop_event.set()
            for thread in threads:
                thread.join(timeout=30)
            self._record_lifecycle(SYSTEM_WORKER_STOPPED, "Worker stopped")

    def _job_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.run_one(timeout=2.0)
            except Exception as exc:  # noqa: BLE001
                # Database or Redis briefly unavailable: pause, don't spin.
                log.error("Job loop error: %s", exc)
                self.stop_event.wait(5)

    def _record_lifecycle(self, event_type: str, message: str) -> None:
        try:
            with session_scope() as session:
                record_event(
                    session, event_type, f"{message} ({self.name}, {self.dispatcher.name})",
                    entity_type="service", entity_id="worker",
                    payload={"worker": self.name, "dispatcher": self.dispatcher.name},
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not record %s: %s", event_type, exc)


def _ms_since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the CommitMail background worker.")
    parser.add_argument("--once", action="store_true", help="drain queued jobs, then exit")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--no-schedule", action="store_true", help="never queue fetches itself")
    parser.add_argument(
        "--no-api", action="store_true", help="do not serve the .ics feed and internal API"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    init_db()
    worker = Worker(concurrency=args.concurrency, schedule=not args.no_schedule)
    log.info("Worker %s using %s dispatch.", worker.name, worker.dispatcher.name)

    if not args.no_api:
        from src.server.runner import start_api_server

        start_api_server()

    if args.once:
        ran = worker.run_until_idle()
        log.info("Ran %d job(s).", ran)
        return 0

    def stop(signum, frame):  # noqa: ANN001
        log.info("Stopping after the current job(s)...")
        worker.stop_event.set()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    worker.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
