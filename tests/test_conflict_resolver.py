"""Tests for follow-up detection and duplicate collapsing (Phase 5)."""
from __future__ import annotations

from datetime import datetime

import pytest
from icalendar import Calendar as ICalendar
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage import database
from src.storage.models import Base
from src.sync import conflict_resolver
from src.sync.conflict_resolver import (
    is_duplicate,
    normalize_subject,
    resolve_conflicts,
    subject_similarity,
)
from src.sync.sync_engine import run_sync

from tests.test_sync_engine import make_commitment, store  # shared builders


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


# --- Subject normalisation -------------------------------------------------

def test_reply_prefixes_and_filler_are_ignored():
    assert normalize_subject("Re: Fwd: the quarterly report") == normalize_subject(
        "Quarterly Report"
    )


def test_similarity_is_high_for_a_reworded_subject():
    assert subject_similarity(
        "Submit the quarterly report", "Submit quarterly report"
    ) >= 0.6


def test_similarity_is_low_for_unrelated_subjects():
    assert subject_similarity("Sprint planning meeting", "Renew the domain") < 0.3


def test_a_vague_subject_does_not_match_a_specific_one():
    """'Meeting' must not swallow 'Meeting about the Q3 budget'."""
    assert subject_similarity("Meeting", "Meeting about the Q3 budget") < 0.6


# --- Duplicate detection ---------------------------------------------------

def test_follow_up_from_the_same_person_is_a_duplicate():
    first = make_commitment(subject="Submit the quarterly report")
    first.email_id = 1
    second = make_commitment(subject="Re: Submit quarterly report")
    second.email_id = 2
    assert is_duplicate(first, second) is True


def test_same_subject_from_a_different_person_is_not_a_duplicate():
    first = make_commitment(counterparty_email="alice@university.edu")
    first.email_id = 1
    second = make_commitment(counterparty_email="bob@university.edu")
    second.email_id = 2
    assert is_duplicate(first, second) is False


def test_different_commitment_types_are_not_merged():
    first = make_commitment(type="meeting")
    first.email_id = 1
    second = make_commitment(type="deadline_on_you")
    second.email_id = 2
    assert is_duplicate(first, second) is False


def test_two_commitments_from_one_email_are_never_merged():
    """The model legitimately finds several obligations in a single message."""
    first = make_commitment(subject="Submit the quarterly report")
    second = make_commitment(subject="Submit the quarterly report")
    first.email_id = second.email_id = 7
    assert is_duplicate(first, second) is False


def test_a_single_shared_word_is_not_enough():
    first = make_commitment(subject="Send report")
    first.email_id = 1
    second = make_commitment(subject="Send invoice")
    second.email_id = 2
    assert is_duplicate(first, second) is False


# --- Resolution over the database ------------------------------------------

def test_follow_up_supersedes_the_original(session):
    original = store(
        session,
        subject="Submit the quarterly report",
        deadline=datetime(2026, 8, 14, 17, 0),
        received_at=datetime(2026, 7, 1, 9, 0),
    )
    follow_up = store(
        session,
        subject="Re: Submit quarterly report",
        deadline=datetime(2026, 8, 21, 17, 0),
        received_at=datetime(2026, 7, 2, 9, 0),
    )
    session.commit()

    resolution = resolve_conflicts(session)
    session.commit()

    assert resolution.superseded == 1
    assert original.status == "superseded"
    assert follow_up.status == "pending"
    assert follow_up.supersedes_id == original.id


def test_the_newer_deadline_is_the_one_that_survives(session):
    store(
        session,
        subject="Submit the quarterly report",
        deadline=datetime(2026, 8, 14, 17, 0),
        received_at=datetime(2026, 7, 1, 9, 0),
    )
    store(
        session,
        subject="Re: Submit quarterly report",
        deadline=datetime(2026, 8, 21, 17, 0),
        received_at=datetime(2026, 7, 2, 9, 0),
    )
    session.commit()
    resolve_conflicts(session)
    session.commit()

    remaining = database.calendar_candidates(session)
    assert [c.deadline for c in remaining] == [datetime(2026, 8, 21, 17, 0)]


def test_the_follow_up_inherits_the_published_event_id(session, tmp_path):
    """The subscriber's existing event is revised, not replaced."""
    original = store(
        session,
        subject="Submit the quarterly report",
        deadline=datetime(2026, 8, 14, 17, 0),
        received_at=datetime(2026, 7, 1, 9, 0),
    )
    session.commit()
    run_sync(session, path=tmp_path / "calendar.ics")
    session.commit()
    published_uid = original.ics_uid
    assert published_uid

    follow_up = store(
        session,
        subject="Re: Submit quarterly report",
        deadline=datetime(2026, 8, 21, 17, 0),
        received_at=datetime(2026, 7, 2, 9, 0),
    )
    session.commit()
    resolve_conflicts(session)
    session.commit()

    assert follow_up.ics_uid == published_uid


def test_a_revised_deadline_updates_the_event_instead_of_duplicating_it(
    session, tmp_path
):
    target = tmp_path / "calendar.ics"
    store(
        session,
        subject="Submit the quarterly report",
        deadline=datetime(2026, 8, 14, 17, 0),
        received_at=datetime(2026, 7, 1, 9, 0),
    )
    session.commit()
    run_sync(session, path=target)
    session.commit()

    store(
        session,
        subject="Re: Submit quarterly report",
        deadline=datetime(2026, 8, 21, 17, 0),
        received_at=datetime(2026, 7, 2, 9, 0),
    )
    session.commit()
    report = run_sync(session, path=target)
    session.commit()

    assert report.superseded == 1
    events = [c for c in ICalendar.from_ical(target.read_bytes()).walk()
              if c.name == "VEVENT"]
    # One obligation, one event — carrying the revised date.
    assert len(events) == 1
    assert events[0].decoded("dtstart") == datetime(2026, 8, 21, 17, 0)


def test_a_thread_revised_twice_collapses_to_one_event(session, tmp_path):
    for day, deadline_day in ((1, 14), (2, 21), (3, 28)):
        store(
            session,
            subject="Submit the quarterly report",
            deadline=datetime(2026, 8, deadline_day, 17, 0),
            received_at=datetime(2026, 7, day, 9, 0),
        )
    session.commit()

    target = tmp_path / "calendar.ics"
    report = run_sync(session, path=target)
    session.commit()

    assert report.superseded == 2
    events = [c for c in ICalendar.from_ical(target.read_bytes()).walk()
              if c.name == "VEVENT"]
    assert len(events) == 1
    assert events[0].decoded("dtstart") == datetime(2026, 8, 28, 17, 0)


def test_unrelated_commitments_are_left_alone(session):
    store(session, subject="Submit the quarterly report")
    store(session, subject="Renew the office domain name")
    store(session, subject="Sprint planning meeting", type="meeting")
    session.commit()

    resolution = resolve_conflicts(session)
    session.commit()

    assert resolution.superseded == 0
    assert len(database.calendar_candidates(session)) == 3


def test_resolution_is_idempotent(session):
    store(
        session,
        subject="Submit the quarterly report",
        received_at=datetime(2026, 7, 1, 9, 0),
    )
    store(
        session,
        subject="Re: Submit quarterly report",
        received_at=datetime(2026, 7, 2, 9, 0),
    )
    session.commit()

    first = resolve_conflicts(session)
    session.commit()
    second = resolve_conflicts(session)
    session.commit()

    assert first.superseded == 1
    assert second.superseded == 0


def test_superseding_is_recorded_in_the_sync_log(session):
    original = store(
        session,
        subject="Submit the quarterly report",
        received_at=datetime(2026, 7, 1, 9, 0),
    )
    store(
        session,
        subject="Re: Submit quarterly report",
        received_at=datetime(2026, 7, 2, 9, 0),
    )
    session.commit()

    resolve_conflicts(session)
    session.commit()

    entries = database.recent_sync_log(session)
    assert any(
        e.commitment_id == original.id and e.action == "deleted" for e in entries
    )


def test_threshold_can_be_tightened(session):
    """Partly-overlapping subjects merge by default but not under a strict cap."""
    store(
        session,
        subject="Submit the quarterly report",
        received_at=datetime(2026, 7, 1, 9, 0),
    )
    store(
        session,
        subject="Re: Submit quarterly report for Q3",
        received_at=datetime(2026, 7, 2, 9, 0),
    )
    session.commit()

    assert resolve_conflicts(session, threshold=0.9).superseded == 0
    # The same pair still merges under the default threshold.
    assert resolve_conflicts(session).superseded == 1
