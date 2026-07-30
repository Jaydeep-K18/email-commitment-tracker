"""Tests for Phase 5 tier logic, retry handling, and feed consistency."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from icalendar import Calendar as ICalendar
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage import database
from src.storage.models import Base, Commitment, RawEmail
from src.sync import sync_engine
from src.sync.sync_engine import decide, review_queue, run_sync


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    db = TestSession()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def make_commitment(**overrides) -> Commitment:
    fields = dict(
        type="deadline_on_you",
        subject="Submit the quarterly report",
        deadline=datetime(2026, 8, 15, 14, 30),
        counterparty_name="Alice Chen",
        counterparty_email="alice@university.edu",
        direction="outgoing",
        evidence_quote="please submit the quarterly report by 15 August",
        confidence=0.95,
        vip_tier="CRITICAL",
        status="pending",
        calendar_synced=False,
        sync_approved=False,
    )
    fields.update(overrides)
    return Commitment(**fields)


_email_seq = iter(range(1, 10_000))


def store(session, *, received_at=None, **overrides) -> Commitment:
    """Persist a commitment together with a source email."""
    index = next(_email_seq)
    email = RawEmail(
        message_id=f"msg-{index}",
        subject="s",
        received_at=received_at or datetime(2026, 7, 1, 9, 0),
    )
    session.add(email)
    session.flush()
    commitment = make_commitment(**overrides)
    commitment.email_id = email.id
    session.add(commitment)
    session.flush()
    return commitment


# --- Tier policy (pure decisions) -----------------------------------------

def test_critical_tier_syncs_automatically():
    decision = decide(make_commitment(vip_tier="CRITICAL"))
    assert decision.should_sync is True
    assert decision.needs_review is False


def test_important_tier_syncs_but_is_flagged_for_review():
    decision = decide(make_commitment(vip_tier="IMPORTANT"))
    assert decision.should_sync is True
    assert decision.needs_review is True


def test_monitor_tier_waits_for_approval():
    decision = decide(make_commitment(vip_tier="MONITOR"))
    assert decision.should_sync is False
    assert decision.awaiting_approval is True
    assert "approval" in decision.reason


def test_monitor_tier_syncs_once_approved():
    decision = decide(make_commitment(vip_tier="MONITOR", sync_approved=True))
    assert decision.should_sync is True


def test_skip_tier_never_syncs():
    decision = decide(make_commitment(vip_tier="SKIP"))
    assert decision.should_sync is False
    # Not merely unapproved — approving it must not be offered as a fix.
    assert decision.awaiting_approval is False


def test_skip_tier_stays_off_the_calendar_even_if_approved():
    assert decide(make_commitment(vip_tier="SKIP", sync_approved=True)).should_sync is False


def test_questions_never_reach_the_calendar_whatever_the_tier():
    for tier in ("CRITICAL", "IMPORTANT", "MONITOR"):
        decision = decide(
            make_commitment(type="question_pending", vip_tier=tier, sync_approved=True)
        )
        assert decision.should_sync is False, tier
        assert "question" in decision.reason


def test_untiered_commitment_fails_safe_and_asks_first():
    """An unrecognised tier must never auto-publish."""
    decision = decide(make_commitment(vip_tier=None))
    assert decision.should_sync is False
    assert decision.awaiting_approval is True


def test_commitment_without_a_deadline_cannot_be_an_event():
    assert decide(make_commitment(deadline=None)).should_sync is False


@pytest.mark.parametrize("status", ["dismissed", "superseded", "fulfilled"])
def test_closed_commitments_do_not_sync(status):
    assert decide(make_commitment(status=status)).should_sync is False


def test_meetings_and_deadlines_from_others_still_sync():
    for kind in ("meeting", "deadline_from_others", "deadline_on_you"):
        assert decide(make_commitment(type=kind)).should_sync is True, kind


# --- Selection over the database ------------------------------------------

def test_calendar_only_contains_eligible_commitments(session):
    critical = store(session, vip_tier="CRITICAL")
    store(session, vip_tier="MONITOR")
    store(session, vip_tier="SKIP")
    store(session, type="question_pending")
    session.commit()

    selected = sync_engine.calendar_commitments(session)
    assert [c.id for c in selected] == [critical.id]


def test_review_queue_offers_only_things_approval_would_unblock(session):
    monitor = store(session, vip_tier="MONITOR")
    store(session, vip_tier="CRITICAL")   # already syncs
    store(session, vip_tier="SKIP")       # approval would not help
    store(session, type="question_pending", vip_tier="MONITOR")  # never syncs
    session.commit()

    assert [c.id for c in review_queue(session)] == [monitor.id]


def test_approving_a_monitor_commitment_puts_it_on_the_calendar(session):
    monitor = store(session, vip_tier="MONITOR")
    session.commit()
    assert sync_engine.calendar_commitments(session) == []

    database.set_sync_approval(session, monitor.id, True)
    session.commit()

    assert [c.id for c in sync_engine.calendar_commitments(session)] == [monitor.id]


# --- Full sync cycle -------------------------------------------------------

def test_run_sync_publishes_only_eligible_commitments(session, tmp_path):
    store(session, vip_tier="CRITICAL")
    store(session, vip_tier="IMPORTANT", subject="Renew the domain")
    store(session, vip_tier="MONITOR", subject="Optional webinar")
    session.commit()
    target = tmp_path / "calendar.ics"

    report = run_sync(session, path=target)
    session.commit()

    assert report.published == 2
    assert report.created == 2
    assert report.flagged == 1            # the IMPORTANT one
    assert report.awaiting_approval == 1  # the MONITOR one
    assert report.ok

    events = [c for c in ICalendar.from_ical(target.read_bytes()).walk()
              if c.name == "VEVENT"]
    assert len(events) == 2
    assert "Optional webinar" not in target.read_text(encoding="utf-8")


def test_second_run_reports_updates_not_new_events(session, tmp_path):
    store(session, vip_tier="CRITICAL")
    session.commit()
    target = tmp_path / "calendar.ics"

    run_sync(session, path=target)
    session.commit()
    second = run_sync(session, path=target)
    session.commit()

    assert second.created == 0
    assert second.updated == 1


def test_dismissing_a_published_commitment_removes_it_from_the_feed(session, tmp_path):
    commitment = store(session, vip_tier="CRITICAL")
    session.commit()
    target = tmp_path / "calendar.ics"

    run_sync(session, path=target)
    session.commit()
    assert commitment.calendar_synced is True

    database.set_commitment_status(session, commitment.id, "dismissed")
    session.commit()
    report = run_sync(session, path=target)
    session.commit()

    assert report.published == 0
    assert commitment.calendar_synced is False
    events = [c for c in ICalendar.from_ical(target.read_bytes()).walk()
              if c.name == "VEVENT"]
    assert events == []
    # The retraction is auditable.
    assert any(e.action == "deleted" for e in database.recent_sync_log(session))


def test_revoking_approval_pulls_the_event_back_off_the_calendar(session, tmp_path):
    commitment = store(session, vip_tier="MONITOR", sync_approved=True)
    session.commit()
    target = tmp_path / "calendar.ics"
    run_sync(session, path=target)
    session.commit()

    database.set_sync_approval(session, commitment.id, False)
    session.commit()
    report = run_sync(session, path=target)
    session.commit()

    assert report.published == 0
    assert commitment.calendar_synced is False


# --- Failure and retry -----------------------------------------------------

def test_failed_publish_is_recorded_and_queued_for_retry(session, tmp_path):
    commitment = store(session, vip_tier="CRITICAL")
    session.commit()
    # A directory where the file should be makes the write fail like a locked
    # or unwritable path would.
    blocked = tmp_path / "calendar.ics"
    blocked.mkdir()

    report = run_sync(session, path=blocked)
    session.commit()

    assert not report.ok
    assert report.failed == 1
    assert commitment.calendar_synced is False

    entry = database.recent_sync_log(session)[0]
    assert entry.status == "failed"
    assert entry.error_message

    queued = database.sync_retry_queue(session)
    assert [c.id for c in queued] == [commitment.id]


def test_a_later_success_clears_the_retry_queue(session, tmp_path):
    store(session, vip_tier="CRITICAL")
    session.commit()
    blocked = tmp_path / "blocked.ics"
    blocked.mkdir()

    run_sync(session, path=blocked)
    session.commit()
    assert database.sync_retry_queue(session)

    report = run_sync(session, path=tmp_path / "calendar.ics")
    session.commit()

    assert report.ok
    assert report.retried == 1
    assert database.sync_retry_queue(session) == []


def test_retry_queue_gives_up_after_the_configured_cap(session, tmp_path):
    store(session, vip_tier="CRITICAL")
    session.commit()
    blocked = tmp_path / "blocked.ics"
    blocked.mkdir()

    for _ in range(4):
        run_sync(session, path=blocked)
        session.commit()

    assert database.sync_retry_queue(session, max_attempts=3) == []


def test_a_failed_publish_leaves_the_previous_feed_in_place(session, tmp_path):
    """Subscribers keep the last good calendar rather than seeing it empty."""
    store(session, vip_tier="CRITICAL")
    session.commit()
    target = tmp_path / "calendar.ics"
    run_sync(session, path=target)
    session.commit()
    good = target.read_bytes()

    blocked = tmp_path / "blocked.ics"
    blocked.mkdir()
    run_sync(session, path=blocked)
    session.commit()

    assert target.read_bytes() == good
