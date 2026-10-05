"""How a job's id gets from "enqueued" to a worker.

Two implementations of the same small interface:

``RedisDispatcher``
    Three keys. ``ready`` is a list workers block on, ``delayed`` a sorted set
    of retries keyed by when they are due, and ``processing`` a list holding the
    ids currently taken. A worker takes an id with ``BLMOVE ready → processing``,
    an atomic move, so an id is never in neither list. That is the "reliable
    queue" pattern: a worker that dies holding a job leaves the id in
    ``processing`` rather than losing it.

``PollingDispatcher``
    No Redis at all: workers ask the jobs table for the next runnable row. It
    reacts within a second or two rather than instantly, and is what runs in the
    tests and whenever Redis is unavailable.

Neither one decides anything. Every id they hand out still has to be *claimed*
against the database (:func:`src.jobs.queue.claim`), which only one worker can
win. So a dispatcher may deliver an id twice — after a crash, or because the
sweeper re-pushed it — and nothing runs twice.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Protocol

from sqlalchemy import or_, select

from src import config
from src.storage.models import Job, utcnow_naive

log = logging.getLogger(__name__)

RUNNABLE = ("queued", "retrying")


class Dispatcher(Protocol):
    name: str

    def push(self, job_id: int, run_at: datetime | None = None) -> None: ...
    def pop(self, timeout: float) -> int | None: ...
    def ack(self, job_id: int) -> None: ...
    def promote_due(self) -> int: ...
    def depth(self) -> dict[str, int]: ...
    def contains(self, job_id: int) -> bool: ...
    def ping(self) -> bool: ...


class RedisDispatcher:
    """Job ids in Redis. See the module docstring for the three keys."""

    name = "redis"

    def __init__(self, client, prefix: str | None = None) -> None:
        self.redis = client
        prefix = prefix or config.REDIS_KEY_PREFIX
        self.ready = f"{prefix}:jobs:ready"
        self.processing = f"{prefix}:jobs:processing"
        self.delayed = f"{prefix}:jobs:delayed"

    @classmethod
    def from_url(cls, url: str) -> "RedisDispatcher":
        import redis

        return cls(redis.Redis.from_url(url, socket_timeout=10, socket_connect_timeout=5))

    def push(self, job_id: int, run_at: datetime | None = None) -> None:
        if run_at is not None and run_at > utcnow_naive():
            # Scored by the moment it becomes due; promote_due() moves it over.
            self.redis.zadd(self.delayed, {str(job_id): _epoch(run_at)})
            return
        if self.contains(job_id):
            return   # already waiting; a second copy would only be skipped later
        self.redis.lpush(self.ready, str(job_id))

    def pop(self, timeout: float) -> int | None:
        value = self.redis.blmove(self.ready, self.processing, timeout, "RIGHT", "LEFT")
        return int(value) if value is not None else None

    def ack(self, job_id: int) -> None:
        self.redis.lrem(self.processing, 1, str(job_id))

    def promote_due(self) -> int:
        """Move retries whose time has come onto the ready list.

        Several workers may run this at once. ``ZREM`` returns 1 to exactly one
        caller per id, and only that caller pushes it, so no id is promoted
        twice — without needing a Lua script.
        """
        due = self.redis.zrangebyscore(self.delayed, "-inf", _epoch(utcnow_naive()), 0, 100)
        promoted = 0
        for raw in due:
            if self.redis.zrem(self.delayed, raw):
                self.redis.lpush(self.ready, raw)
                promoted += 1
        return promoted

    def contains(self, job_id: int) -> bool:
        member = str(job_id)
        return (
            self.redis.lpos(self.ready, member) is not None
            or self.redis.lpos(self.processing, member) is not None
            or self.redis.zscore(self.delayed, member) is not None
        )

    def prune_processing(self, running_ids: set[int]) -> int:
        """Forget ids left in ``processing`` by workers that died.

        Only bookkeeping: the job itself is recovered from its expired lease in
        the database, and re-dispatched from there.
        """
        removed = 0
        for raw in self.redis.lrange(self.processing, 0, -1):
            if int(raw) not in running_ids:
                removed += self.redis.lrem(self.processing, 1, raw)
        return removed

    def depth(self) -> dict[str, int]:
        return {
            "ready": int(self.redis.llen(self.ready)),
            "delayed": int(self.redis.zcard(self.delayed)),
            "processing": int(self.redis.llen(self.processing)),
        }

    def ping(self) -> bool:
        try:
            return bool(self.redis.ping())
        except Exception:  # noqa: BLE001 - health check
            return False


class PollingDispatcher:
    """No Redis: the jobs table is the queue."""

    name = "database"

    def __init__(self, session_factory, poll_interval: float = 1.0) -> None:
        self.session_factory = session_factory
        self.poll_interval = poll_interval
        self._wake = threading.Event()

    def push(self, job_id: int, run_at: datetime | None = None) -> None:
        self._wake.set()   # a worker in this process can look straight away

    def pop(self, timeout: float) -> int | None:
        job_id = self._next_runnable()
        if job_id is not None:
            return job_id
        self._wake.wait(min(timeout, self.poll_interval))
        self._wake.clear()
        return self._next_runnable()

    def _next_runnable(self) -> int | None:
        now = utcnow_naive()
        with self.session_factory() as session:
            return session.scalar(
                select(Job.id)
                .where(
                    Job.status.in_(RUNNABLE),
                    or_(Job.next_attempt_at.is_(None), Job.next_attempt_at <= now),
                )
                .order_by(Job.priority.desc(), Job.id)
                .limit(1)
            )

    def ack(self, job_id: int) -> None:
        pass

    def promote_due(self) -> int:
        return 0   # due-ness is part of the query

    def contains(self, job_id: int) -> bool:
        return True   # every runnable row is, by definition, "queued"

    def depth(self) -> dict[str, int]:
        return {}

    def ping(self) -> bool:
        return True


def _epoch(moment: datetime) -> float:
    """Seconds since the epoch for a naive-UTC datetime."""
    return (moment - datetime(1970, 1, 1)).total_seconds()


# --- The process-wide dispatcher ------------------------------------------

_dispatcher: Dispatcher | None = None
_lock = threading.Lock()


def get_dispatcher() -> Dispatcher:
    """Redis when configured and reachable, otherwise the polling fallback.

    Decided once per process. A Redis that is down at startup is logged loudly,
    not fatal: the app keeps working on the slower path.
    """
    global _dispatcher
    with _lock:
        if _dispatcher is None:
            _dispatcher = _build()
        return _dispatcher


def set_dispatcher(dispatcher: Dispatcher | None) -> None:
    """Install a specific dispatcher (tests), or None to rebuild on next use."""
    global _dispatcher
    with _lock:
        _dispatcher = dispatcher


def _build() -> Dispatcher:
    from src.storage.database import SessionLocal

    if config.REDIS_URL:
        try:
            dispatcher = RedisDispatcher.from_url(config.REDIS_URL)
            if dispatcher.ping():
                log.info("Jobs dispatched through Redis.")
                return dispatcher
        except Exception as exc:  # noqa: BLE001
            log.warning("Redis unavailable (%s).", exc)
        log.warning("Redis is configured but unreachable; polling the jobs table instead.")
    return PollingDispatcher(SessionLocal)


def wait_until(predicate, timeout: float, interval: float = 0.05) -> bool:
    """Poll ``predicate`` until it is true or ``timeout`` passes. For tests."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
