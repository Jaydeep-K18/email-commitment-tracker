"""v2 phase 3: the job queue, its retries, and the no-duplicates guarantees.

The tests that matter most here are about what happens when things go wrong
at the worst moment: a job delivered twice, a worker dying mid-run, Redis
losing its data, and — the one users would actually notice — a Google insert
that succeeded but whose response never arrived.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import fakeredis
import httplib2
import pytest
from googleapiclient.errors import HttpError
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import sessionmaker

from src.events import recorder
from src.extraction.ollama_client import OllamaUnavailableError
from src.jobs import dispatch, handlers, queue
from src.jobs.backoff import backoff_seconds
from src.jobs.dispatch import PollingDispatcher, RedisDispatcher
from src.jobs.handlers import HANDLERS, JobContext, Services
from src.jobs.worker import Worker
from src.storage import database
from src.storage.models import Base, Commitment, Event, Job, JobAttempt, RawEmail, User, utcnow_naive
from src.sync import google_calendar


# --- Fixtures -----------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(User.__table__.insert().values(id=1, email="owner@example.com", is_admin=True))
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(
        database, "SessionLocal", sessionmaker(bind=engine, future=True, expire_on_commit=False)
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def polling(db):
    dispatcher = PollingDispatcher(database.SessionLocal, poll_interval=0.01)
    dispatch.set_dispatcher(dispatcher)
    yield dispatcher
    dispatch.set_dispatcher(None)


@pytest.fixture()
def redis_dispatcher(db):
    dispatcher = RedisDispatcher(fakeredis.FakeRedis(), prefix="test")
    dispatch.set_dispatcher(dispatcher)
    yield dispatcher
    dispatch.set_dispatcher(None)


@pytest.fixture()
def fake_handler(monkeypatch):
    """A job type whose behaviour each test scripts: a list of outcomes."""
    script: list = []
    calls: list[dict] = []

    def run(payload, ctx):
        calls.append({"payload": payload, "attempt": ctx.attempt})
        outcome = script.pop(0) if script else {"ok": True}
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setitem(HANDLERS, "test_job", run)
    return SimpleNamespace(script=script, calls=calls)


def session():
    return database.SessionLocal()


def job(job_id) -> Job:
    with session() as s:
        return s.get(Job, job_id)


def event_types(**filters) -> list[str]:
    with session() as s:
        query = select(Event.type).order_by(Event.id)
        for name, value in filters.items():
            query = query.where(getattr(Event, name) == value)
        return list(s.scalars(query))


def add(job_type="test_job", payload=None, key="k1", **options) -> int:
    with database.session_scope() as s:
        return queue.enqueue(s, job_type, payload or {}, key=key, **options).job_id


def make_due(job_id):
    """Skip the backoff wait without sleeping through it."""
    with database.session_scope() as s:
        s.execute(update(Job).where(Job.id == job_id).values(next_attempt_at=utcnow_naive()))


def worker(dispatcher, **kwargs) -> Worker:
    return Worker(dispatcher=dispatcher, schedule=False, **kwargs)


# --- Backoff ------------------------------------------------------------------

def test_backoff_doubles_with_each_attempt_and_is_capped():
    no_jitter = lambda: 1.0   # noqa: E731 - always the top of the range
    delays = [backoff_seconds(n, base=10, cap=100, rng=no_jitter) for n in range(1, 7)]
    assert delays == [10, 20, 40, 80, 100, 100]


def test_jitter_spreads_retries_but_never_below_half():
    """Without jitter, every job that failed in one outage retries in the same
    instant it ends, and hits the recovering service all at once."""
    assert backoff_seconds(3, base=10, cap=100, rng=lambda: 0.0) == 20
    assert backoff_seconds(3, base=10, cap=100, rng=lambda: 1.0) == 40


# --- Enqueueing ---------------------------------------------------------------

def test_the_same_key_enqueues_one_job_however_often_it_is_asked(polling):
    with database.session_scope() as s:
        first = queue.enqueue(s, "test_job", {}, key="process_email:7")
        second = queue.enqueue(s, "test_job", {}, key="process_email:7")

    assert first.created is True
    assert second == queue.Enqueued(first.job_id, False)
    with session() as s:
        assert s.scalar(select(func.count()).select_from(Job)) == 1
    assert event_types(type=recorder.JOB_QUEUED) == [recorder.JOB_QUEUED]


def test_coalesced_work_reuses_an_active_job_but_not_a_finished_one(polling, fake_handler):
    with database.session_scope() as s:
        first = queue.enqueue_unless_active(s, "test_job")
        again = queue.enqueue_unless_active(s, "test_job")
    assert again == queue.Enqueued(first.job_id, False)

    worker(polling).run_until_idle()

    with database.session_scope() as s:
        later = queue.enqueue_unless_active(s, "test_job")
    assert later.created is True and later.job_id != first.job_id


def test_a_job_is_only_dispatched_once_its_transaction_commits(redis_dispatcher):
    s = session()
    queue.enqueue(s, "test_job", {}, key="never")
    s.rollback()
    s.close()

    assert redis_dispatcher.depth()["ready"] == 0


# --- Claiming -------------------------------------------------------------------

def test_two_workers_cannot_both_claim_one_job(polling):
    job_id = add()
    with database.session_scope() as s:
        first = queue.claim(s, job_id, "worker-a")
    with database.session_scope() as s:
        second = queue.claim(s, job_id, "worker-b")

    assert first is not None
    assert second is None
    assert job(job_id).locked_by == "worker-a"


def test_a_job_delivered_twice_runs_once(redis_dispatcher, fake_handler):
    job_id = add()
    redis_dispatcher.redis.lpush(redis_dispatcher.ready, str(job_id))   # a duplicate id

    worker(redis_dispatcher).run_until_idle()

    assert len(fake_handler.calls) == 1
    assert job(job_id).status == "succeeded"


# --- Running, failing, retrying ------------------------------------------------

def test_a_successful_job_records_its_attempt_and_result(polling, fake_handler):
    fake_handler.script.append({"answer": 42})
    job_id = add()

    worker(polling).run_until_idle()

    done = job(job_id)
    assert (done.status, done.attempts, done.result) == ("succeeded", 1, {"answer": 42})
    with session() as s:
        [attempt] = s.scalars(select(JobAttempt)).all()
    assert attempt.status == "succeeded" and attempt.duration_ms is not None
    assert event_types(entity_type="job") == [
        recorder.JOB_QUEUED, recorder.JOB_STARTED, recorder.JOB_COMPLETED,
    ]


def test_a_failure_is_retried_after_a_backoff_then_succeeds(polling, fake_handler):
    fake_handler.script.append(ConnectionError("Ollama refused the connection"))
    job_id = add()
    w = worker(polling)

    w.run_until_idle()
    waiting = job(job_id)
    assert waiting.status == "retrying"
    assert waiting.next_attempt_at > utcnow_naive()       # not hammered immediately
    assert "ConnectionError" in waiting.last_error

    make_due(job_id)
    w.run_until_idle()

    done = job(job_id)
    assert (done.status, done.attempts) == ("succeeded", 2)
    with session() as s:
        statuses = list(s.scalars(select(JobAttempt.status).order_by(JobAttempt.attempt)))
    assert statuses == ["failed", "succeeded"]          # the retry history


def test_a_permanent_error_is_not_retried(polling, fake_handler):
    fake_handler.script.append(queue.PermanentJobError("no such commitment"))
    job_id = add()

    worker(polling).run_until_idle()

    failed = job(job_id)
    assert (failed.status, failed.attempts) == ("failed", 1)
    assert recorder.JOB_FAILED in event_types()


def test_a_job_that_keeps_failing_ends_up_failed(polling, fake_handler):
    fake_handler.script.extend([TimeoutError("slow")] * 3)
    job_id = add(max_attempts=3)
    w = worker(polling)

    for _ in range(3):
        w.run_until_idle()
        make_due(job_id)

    failed = job(job_id)
    assert (failed.status, failed.attempts) == ("failed", 3)
    assert event_types(type=recorder.JOB_RETRYING) == [recorder.JOB_RETRYING] * 2


def test_manual_retry_gives_a_failed_job_a_fresh_budget(polling, fake_handler):
    fake_handler.script.append(queue.PermanentJobError("Ollama is not installed"))
    job_id = add()
    w = worker(polling)
    w.run_until_idle()
    assert job(job_id).status == "failed"

    with database.session_scope() as s:
        assert queue.retry_job(s, job_id, source="server") is True
    w.run_until_idle()

    done = job(job_id)
    assert (done.status, done.attempts) == ("succeeded", 2)
    assert recorder.JOB_RETRIED in event_types()


def test_only_failed_or_cancelled_jobs_can_be_manually_retried(polling):
    job_id = add()
    with database.session_scope() as s:
        assert queue.retry_job(s, job_id) is False


# --- Crash recovery ---------------------------------------------------------------

def test_a_job_whose_worker_died_is_handed_back(polling, fake_handler):
    job_id = add()
    with database.session_scope() as s:
        queue.claim(s, job_id, "worker-that-dies")
    with database.session_scope() as s:   # its lease runs out
        s.execute(update(Job).where(Job.id == job_id).values(
            locked_until=utcnow_naive() - timedelta(seconds=1)))

    w = worker(polling)
    w.maintain()
    assert job(job_id).status == "retrying"
    assert "stopped responding" in job(job_id).last_error

    make_due(job_id)
    w.run_until_idle()
    assert job(job_id).status == "succeeded"


def test_jobs_survive_redis_losing_everything(redis_dispatcher, fake_handler):
    """Redis carries ids, not state. Wipe it and the sweeper rebuilds the queue
    from the jobs table."""
    job_id = add()
    with database.session_scope() as s:   # old enough to be swept
        s.execute(update(Job).where(Job.id == job_id).values(
            updated_at=utcnow_naive() - timedelta(minutes=5)))
    redis_dispatcher.redis.flushall()

    worker(redis_dispatcher).run_until_idle()

    assert job(job_id).status == "succeeded"


# --- Redis dispatcher -----------------------------------------------------------

def test_a_retry_due_now_skips_the_wait(redis_dispatcher):
    redis_dispatcher.push(4, utcnow_naive() - timedelta(seconds=1))
    assert redis_dispatcher.depth() == {"ready": 1, "delayed": 0, "processing": 0}


def make_overdue(dispatcher, job_id):
    """A retry that was scheduled earlier and whose moment has now passed."""
    dispatcher.redis.zadd(dispatcher.delayed, {str(job_id): 0})


def test_a_delayed_retry_waits_in_the_sorted_set_until_due(redis_dispatcher):
    redis_dispatcher.push(5, utcnow_naive() + timedelta(minutes=10))
    make_overdue(redis_dispatcher, 6)
    assert redis_dispatcher.depth() == {"ready": 0, "delayed": 2, "processing": 0}

    assert redis_dispatcher.promote_due() == 1
    assert redis_dispatcher.pop(0.1) == 6
    assert redis_dispatcher.pop(0.1) is None


def test_two_workers_promoting_at_once_promote_each_retry_once(redis_dispatcher):
    other = RedisDispatcher(redis_dispatcher.redis, prefix="test")
    make_overdue(redis_dispatcher, 9)

    assert redis_dispatcher.promote_due() + other.promote_due() == 1
    assert redis_dispatcher.depth()["ready"] == 1


def test_a_taken_job_is_held_in_processing_until_acknowledged(redis_dispatcher):
    redis_dispatcher.push(3)
    assert redis_dispatcher.pop(0.1) == 3
    assert redis_dispatcher.depth()["processing"] == 1
    redis_dispatcher.ack(3)
    assert redis_dispatcher.depth()["processing"] == 0


# --- Handlers: analysing email -----------------------------------------------------

BODY = "Hi,\n\nPlease submit your final project report by 15th August.\n"


class FakeModel:
    def __init__(self, up=True):
        self.up = up
        self.calls = 0

    def ensure_ready(self):
        if not self.up:
            raise OllamaUnavailableError("Ollama is not reachable")

    def generate(self, prompt, system=None, schema=None, **kwargs):
        self.calls += 1
        return json.dumps({"commitments": [{
            "type": "deadline_on_you", "subject": "Submit final project report",
            "deadline": "2026-08-15", "counterparty": "Dr. Alice Chen",
            "direction": "outgoing",
            "evidence_quote": "Please submit your final project report by 15th August",
            "confidence": 0.95,
        }]})


def stored_email(**fields) -> int:
    defaults = dict(message_id=f"e-{id(fields)}", subject="Report", body_text=BODY,
                    sender_email="alice@university.edu", vip_tier="CRITICAL",
                    received_at=datetime(2026, 8, 1, 9))
    defaults.update(fields)
    with database.session_scope() as s:
        email = RawEmail(**defaults)
        s.add(email)
        s.flush()
        return email.id


def services(model=None, **overrides) -> Services:
    defaults = dict(
        fetch=lambda: SimpleNamespace(fetched=0, new=0),
        sync_sent=lambda: 0,
        ollama_client=lambda: model or FakeModel(),
        google_service=lambda: None,
        google_available=lambda: False,
    )
    return Services(**{**defaults, **overrides})


def test_analysing_an_email_stores_its_commitments_and_queues_a_publish(polling):
    email_id = stored_email()
    job_id = add("process_email", {"email_id": email_id}, key=f"process_email:{email_id}")

    Worker(dispatcher=polling, services=services(), schedule=False).process(job_id)

    with session() as s:
        assert s.get(RawEmail, email_id).processed is True
        assert s.scalar(select(func.count()).select_from(Commitment)) == 1
        assert s.scalar(select(Job.id).where(Job.type == "publish_calendar")) is not None


def test_an_email_already_analysed_is_not_sent_to_the_model_again(polling):
    """The case after a crash between the work committing and the job finishing."""
    email_id = stored_email(processed=True)
    model = FakeModel()
    ctx = JobContext(1, 1, 5, services(model))

    result = handlers.process_email({"email_id": email_id}, ctx)

    assert result == {"skipped": "already analyzed"}
    assert model.calls == 0


def test_while_ollama_is_down_the_analysis_backs_off_instead_of_failing(polling):
    email_id = stored_email()
    job_id = add("process_email", {"email_id": email_id}, key=f"process_email:{email_id}")

    Worker(dispatcher=polling, services=services(FakeModel(up=False)), schedule=False).process(job_id)

    assert job(job_id).status == "retrying"
    with session() as s:
        assert s.get(RawEmail, email_id).processed is False


def test_two_fetches_queue_each_email_for_analysis_once(polling):
    first, second = stored_email(message_id="a"), stored_email(message_id="b")
    stored_email(message_id="skip", vip_tier="SKIP")      # never sent to the model
    ctx = JobContext(1, 1, 5, services())

    handlers.fetch_mailbox({}, ctx)
    handlers.fetch_mailbox({}, ctx)

    with session() as s:
        keys = sorted(s.scalars(select(Job.idempotency_key).where(Job.type == "process_email")))
    assert keys == sorted([f"process_email:{first}", f"process_email:{second}"])


# --- Handlers: Google Calendar ---------------------------------------------------

def http_error(status: int, message: str = "") -> HttpError:
    body = json.dumps({"error": {"message": message or f"HTTP {status}"}}).encode()
    return HttpError(httplib2.Response({"status": str(status)}), body)


class FakeCalendar:
    """A Google Calendar that holds events by id, honouring client-chosen ids.

    ``lose_next_response`` reproduces the case that matters: the insert reaches
    Google and is stored, but the response is lost on the way back.
    """

    def __init__(self):
        self.store: dict[str, dict] = {}
        self.lose_next_response = False
        self.reject_with: HttpError | None = None

    def events(self):
        return self

    def insert(self, calendarId, body):  # noqa: N803
        def execute():
            if self.reject_with:
                raise self.reject_with
            event_id = body["id"]
            if event_id in self.store:
                raise http_error(409, "The requested identifier already exists.")
            self.store[event_id] = dict(body)
            if self.lose_next_response:
                self.lose_next_response = False
                raise ConnectionResetError("connection reset before the response arrived")
            return {"id": event_id}
        return SimpleNamespace(execute=execute)

    def patch(self, calendarId, eventId, body):  # noqa: N803
        def execute():
            if eventId not in self.store:
                raise http_error(404)
            self.store[eventId].update(body)
            return {"id": eventId}
        return SimpleNamespace(execute=execute)

    def delete(self, calendarId, eventId):  # noqa: N803
        def execute():
            if self.store.pop(eventId, None) is None:
                raise http_error(410)
        return SimpleNamespace(execute=execute)


def published_commitment(**fields) -> int:
    email_id = stored_email(message_id=f"c-{id(fields)}", processed=True)
    defaults = dict(email_id=email_id, type="deadline_on_you", subject="Send report",
                    deadline=datetime(2026, 9, 10, 17, 0), evidence_quote="send it",
                    confidence=0.9, vip_tier="CRITICAL")
    defaults.update(fields)
    with database.session_scope() as s:
        commitment = Commitment(**defaults)
        s.add(commitment)
        s.flush()
        return commitment.id


def google_services(calendar: FakeCalendar) -> Services:
    return services(google_service=lambda: calendar, google_available=lambda: True)


def push_job(commitment_id) -> int:
    with session() as s:
        content = google_calendar.event_content_hash(s.get(Commitment, commitment_id))
    return add("push_google_event", {"commitment_id": commitment_id, "content_hash": content},
               key=f"gcal_push:{commitment_id}:{content}")


def test_a_lost_response_cannot_create_a_second_google_event(polling, tmp_path):
    """The insert reached Google; the reply did not. The retry must not insert
    again — the chosen event id makes Google answer 409, and it becomes an update."""
    calendar = FakeCalendar()
    calendar.lose_next_response = True
    commitment_id = published_commitment()
    job_id = push_job(commitment_id)
    w = Worker(dispatcher=polling, services=google_services(calendar), schedule=False)

    w.process(job_id)
    assert job(job_id).status == "retrying"
    assert len(calendar.store) == 1          # it did get through the first time

    make_due(job_id)
    w.run_until_idle()

    assert job(job_id).status == "succeeded"
    assert len(calendar.store) == 1          # and still exactly one event
    with session() as s:
        stored = s.get(Commitment, commitment_id)
        assert stored.gcal_event_id == next(iter(calendar.store))
        assert stored.gcal_synced_hash == google_calendar.event_content_hash(stored)


def test_event_ids_are_stable_valid_and_differ_between_installations():
    one = google_calendar.deterministic_event_id("install-a", 7)
    assert one == google_calendar.deterministic_event_id("install-a", 7)
    assert one != google_calendar.deterministic_event_id("install-b", 7)
    assert set(one) <= set("abcdefghijklmnopqrstuv0123456789")   # Google's base32hex
    assert 5 <= len(one) <= 1024


def test_publishing_queues_a_push_only_for_commitments_that_changed(polling, tmp_path, monkeypatch):
    monkeypatch.setattr(database.config, "ICS_PATH", tmp_path / "c.ics")
    calendar = FakeCalendar()
    commitment_id = published_commitment()
    ctx = JobContext(1, 1, 5, google_services(calendar))
    w = Worker(dispatcher=polling, services=google_services(calendar), schedule=False)

    assert handlers.publish_calendar({}, ctx)["google_pushes_queued"] == 1
    w.run_until_idle()
    assert handlers.publish_calendar({}, ctx)["google_pushes_queued"] == 0   # unchanged

    with database.session_scope() as s:
        s.get(Commitment, commitment_id).subject = "Send the revised report"
    assert handlers.publish_calendar({}, ctx)["google_pushes_queued"] == 1
    w.run_until_idle()
    assert next(iter(calendar.store.values()))["summary"].endswith("Send the revised report")


def test_a_push_queued_for_old_content_steps_aside(polling):
    commitment_id = published_commitment()
    ctx = JobContext(1, 1, 5, google_services(FakeCalendar()))

    result = handlers.push_google_event(
        {"commitment_id": commitment_id, "content_hash": "stale"}, ctx
    )
    assert result == {"skipped": "superseded by newer content"}


def test_google_rejecting_an_event_fails_it_without_pointless_retries(polling):
    calendar = FakeCalendar()
    calendar.reject_with = http_error(400, "Invalid start time")
    commitment_id = published_commitment()
    job_id = push_job(commitment_id)

    Worker(dispatcher=polling, services=google_services(calendar), schedule=False).process(job_id)

    failed = job(job_id)
    assert (failed.status, failed.attempts) == ("failed", 1)
    assert recorder.CALENDAR_EVENT_FAILED in event_types()


def test_google_rate_limits_and_outages_are_retried(polling):
    assert google_calendar.is_retryable(http_error(503))
    assert google_calendar.is_retryable(http_error(429))
    assert google_calendar.is_retryable(http_error(403, "rateLimitExceeded"))
    assert not google_calendar.is_retryable(http_error(403, "forbidden"))
    assert not google_calendar.is_retryable(http_error(400))


def test_removing_an_event_that_is_already_gone_succeeds(polling):
    commitment_id = published_commitment(status="dismissed", gcal_event_id="ectgone")
    ctx = JobContext(1, 1, 5, google_services(FakeCalendar()))

    result = handlers.remove_google_event(
        {"commitment_id": commitment_id, "event_id": "ectgone"}, ctx
    )
    assert result == {"removed": "ectgone"}
    with session() as s:
        assert s.get(Commitment, commitment_id).gcal_event_id is None


def test_an_unknown_job_type_fails_permanently(polling):
    job_id = add("no_such_job")
    Worker(dispatcher=polling, services=services(), schedule=False).process(job_id)
    assert job(job_id).status == "failed"


def test_the_worker_schedules_mail_checks_as_ordinary_jobs(polling, monkeypatch):
    w = Worker(dispatcher=polling, services=services(), schedule=True)
    w.maintain()
    w.maintain()   # not due again yet

    with session() as s:
        types = sorted(s.scalars(select(Job.type)))
    assert types == ["enforce_retention", "fetch_mailbox", "publish_calendar"]
