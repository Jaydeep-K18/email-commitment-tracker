"""Tests for the background scheduler cycle (Phase 6).

Every stage is stubbed: these tests are about the cycle's control flow — what
happens when a stage fails — not about IMAP, Ollama, or the calendar, which
have their own tests.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest

from src.collection import scheduler
from src.collection.scheduler import CycleResult, SchedulerHandle, run_cycle


@dataclass
class FakeFetch:
    fetched: int = 5
    new: int = 2


@dataclass
class FakeStats:
    commitments_stored: int = 3


@dataclass
class FakeReport:
    published: int = 4
    superseded: int = 1
    errors: list[str] = field(default_factory=list)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    """Keep the cycle away from the real database, inbox, and LLM."""
    monkeypatch.setattr(scheduler, "init_db", lambda: None)

    @contextmanager
    def fake_scope():
        yield object()

    monkeypatch.setattr(scheduler, "session_scope", fake_scope)


@pytest.fixture()
def stub_stages(monkeypatch):
    """Install working stubs for all three stages; tests override as needed."""
    import src.collection.email_fetcher as fetcher
    import src.extraction.pipeline as pipeline
    import src.sync.sync_engine as engine

    monkeypatch.setattr(fetcher, "fetch_and_store", lambda: FakeFetch())
    monkeypatch.setattr(pipeline, "process_pending_emails", lambda **_: FakeStats())
    monkeypatch.setattr(engine, "run_sync", lambda session: FakeReport())
    return fetcher, pipeline, engine


# --- The happy path --------------------------------------------------------

def test_a_full_cycle_reports_every_stage(stub_stages):
    result = run_cycle()

    assert result.ok
    assert result.emails_fetched == 5
    assert result.emails_new == 2
    assert result.commitments_extracted == 3
    assert result.events_published == 4
    assert result.superseded == 1


def test_summary_reads_as_a_sentence(stub_stages):
    summary = run_cycle().summary()
    assert "2 new email(s)" in summary
    assert "3 commitment(s)" in summary
    assert "4 calendar event(s)" in summary


def test_stages_can_be_skipped(stub_stages):
    result = run_cycle(fetch=False, extract=False)
    assert result.emails_fetched == 0
    assert result.commitments_extracted == 0
    assert result.events_published == 4


def test_extraction_can_be_disabled_by_config(stub_stages, monkeypatch):
    monkeypatch.setattr(scheduler.config, "SCHEDULER_RUN_EXTRACTION", False)
    result = run_cycle()
    assert result.commitments_extracted == 0
    assert result.emails_fetched == 5  # fetching still happens


# --- Stage isolation -------------------------------------------------------

def test_an_unreachable_inbox_does_not_stop_the_calendar_resyncing(stub_stages):
    fetcher, _, _ = stub_stages

    def boom():
        raise OSError("network is unreachable")

    fetcher.fetch_and_store = boom

    result = run_cycle()

    assert not result.ok
    assert result.errors[0][0] == "fetch"
    # The rest of the pipeline still ran.
    assert result.commitments_extracted == 3
    assert result.events_published == 4


def test_a_dead_llm_leaves_email_queued_but_still_syncs(stub_stages):
    _, pipeline, _ = stub_stages

    def boom(**_):
        raise RuntimeError("Ollama is not running")

    pipeline.process_pending_emails = boom

    result = run_cycle()

    assert [stage for stage, _ in result.errors] == ["extract"]
    assert result.emails_new == 2
    assert result.events_published == 4


def test_a_failing_sync_is_reported(stub_stages):
    _, _, engine = stub_stages

    def boom(session):
        raise OSError("disk full")

    engine.run_sync = boom

    result = run_cycle()
    assert [stage for stage, _ in result.errors] == ["sync"]
    assert "disk full" in result.errors[0][1]


def test_sync_errors_from_the_report_are_surfaced(stub_stages):
    _, _, engine = stub_stages
    engine.run_sync = lambda session: FakeReport(errors=["could not write file"])

    result = run_cycle()
    assert not result.ok
    assert result.errors == [("sync", "could not write file")]


def test_every_stage_failing_still_returns_a_result(stub_stages):
    fetcher, pipeline, engine = stub_stages

    def boom(*args, **kwargs):
        raise RuntimeError("nope")

    fetcher.fetch_and_store = boom
    pipeline.process_pending_emails = boom
    engine.run_sync = boom

    result = run_cycle()
    assert [stage for stage, _ in result.errors] == ["fetch", "extract", "sync"]
    assert "failed" in result.summary()


# --- The handle ------------------------------------------------------------

def test_handle_records_the_last_run(stub_stages):
    handle = SchedulerHandle()
    assert handle.last_result is None

    result = handle.run_now()

    assert isinstance(result, CycleResult)
    assert handle.last_result is result
    assert handle.last_run_at is not None
    assert len(handle.history) == 1


def test_handle_history_stays_bounded(stub_stages):
    handle = SchedulerHandle()
    for _ in range(25):
        handle.run_now()
    assert len(handle.history) == 20


def test_starting_and_stopping_the_schedule(stub_stages):
    handle = SchedulerHandle(interval_minutes=5)
    assert handle.running is False
    assert handle.next_run_at is None

    handle.start()
    try:
        assert handle.running is True
        assert handle.next_run_at is not None
    finally:
        handle.shutdown()

    assert handle.running is False


def test_starting_twice_is_harmless(stub_stages):
    handle = SchedulerHandle(interval_minutes=5)
    handle.start()
    try:
        first = handle._scheduler
        handle.start()
        assert handle._scheduler is first  # not replaced
    finally:
        handle.shutdown()


def test_shutdown_without_start_is_harmless():
    SchedulerHandle().shutdown()
