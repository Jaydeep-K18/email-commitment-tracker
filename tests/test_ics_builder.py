"""Tests for .ics generation, the publish flow, and the calendar server."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from icalendar import Calendar as ICalendar
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage import database
from src.storage.models import Base, Commitment, RawEmail, SyncLog
from src.sync import ics_builder
from src.sync.ics_builder import (
    build_event,
    build_summary,
    event_uid,
    is_all_day,
    render_ics,
)


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
        id=1,
        email_id=53,
        type="deadline_on_you",
        subject="Submit final project report",
        deadline=datetime(2026, 8, 15, 14, 30),
        counterparty_name="Dr. Alice Chen",
        counterparty_email="alice@university.edu",
        direction="outgoing",
        evidence_quote="please submit your final project report by 15th August",
        confidence=0.95,
        vip_tier="CRITICAL",
        status="pending",
        calendar_synced=False,
        created_at=datetime(2026, 7, 30, 12, 0),
    )
    fields.update(overrides)
    return Commitment(**fields)


def parse(content: bytes) -> ICalendar:
    return ICalendar.from_ical(content)


def events_of(cal: ICalendar) -> list:
    return [c for c in cal.walk() if c.name == "VEVENT"]


# --- Calendar structure ---------------------------------------------------

def test_generated_calendar_is_parseable_and_well_formed():
    content = render_ics([make_commitment()])
    cal = parse(content)

    assert cal.get("version") == "2.0"
    assert "Email Commitment Tracker" in str(cal.get("prodid"))
    assert content.startswith(b"BEGIN:VCALENDAR")
    assert content.rstrip().endswith(b"END:VCALENDAR")
    assert len(events_of(cal)) == 1


def test_calendar_advertises_a_refresh_interval_for_subscribers():
    content = render_ics([make_commitment()])
    cal = parse(content)
    assert cal.get("refresh-interval") is not None
    assert cal.get("x-published-ttl") is not None
    # Must be an RFC 5545 duration; a bare timedelta renders "1:00:00" and
    # makes the whole feed unparseable.
    assert b"REFRESH-INTERVAL;VALUE=DURATION:PT1H" in content


def test_empty_calendar_is_still_valid():
    """A user with no commitments must still get a subscribable feed."""
    content = render_ics([])
    cal = parse(content)
    assert events_of(cal) == []
    assert content.startswith(b"BEGIN:VCALENDAR")


def test_commitments_without_a_deadline_are_skipped():
    content = render_ics(
        [make_commitment(), make_commitment(id=2, deadline=None)]
    )
    assert len(events_of(parse(content))) == 1


# --- Event content --------------------------------------------------------

def test_timed_event_uses_floating_local_time_not_utc():
    """10:00 in the email must display as 10:00, so no Z suffix and no TZID."""
    content = render_ics([make_commitment(deadline=datetime(2026, 8, 8, 10, 0))])
    assert b"DTSTART:20260808T100000" in content
    assert b"DTSTART:20260808T100000Z" not in content
    assert b"TZID" not in content


def test_timed_event_spans_one_hour():
    event = build_event(make_commitment(deadline=datetime(2026, 8, 15, 14, 30)))
    assert event.decoded("dtstart") == datetime(2026, 8, 15, 14, 30)
    assert event.decoded("dtend") == datetime(2026, 8, 15, 15, 30)


def test_midnight_deadline_becomes_an_all_day_event():
    assert is_all_day(datetime(2026, 8, 4, 0, 0))
    assert not is_all_day(datetime(2026, 8, 4, 9, 0))

    event = build_event(make_commitment(deadline=datetime(2026, 8, 4, 0, 0)))
    assert event.decoded("dtstart") == date(2026, 8, 4)
    # DTEND is exclusive, so a one-day event ends the next day.
    assert event.decoded("dtend") == date(2026, 8, 5)


def test_event_description_carries_the_evidence_for_traceability():
    event = build_event(make_commitment())
    description = str(event.get("description"))

    assert "please submit your final project report" in description
    assert "alice@university.edu" in description
    assert "CRITICAL" in description
    assert "#53" in description
    assert "95%" in description


def test_summary_marks_commitments_owed_to_you():
    assert build_summary(make_commitment()) == "Submit final project report"

    waiting = make_commitment(
        type="deadline_from_others",
        subject="design files",
        counterparty_name="Ravi",
    )
    assert build_summary(waiting) == "Waiting: Ravi — design files"


def test_event_has_a_reminder_alarm():
    event = build_event(make_commitment(deadline=datetime(2026, 8, 15, 14, 30)))
    alarms = [c for c in event.walk() if c.name == "VALARM"]
    assert len(alarms) == 1
    assert alarms[0].decoded("trigger") == timedelta(minutes=-30)


def test_all_day_event_reminds_a_day_ahead():
    event = build_event(make_commitment(deadline=datetime(2026, 8, 4, 0, 0)))
    alarm = [c for c in event.walk() if c.name == "VALARM"][0]
    assert alarm.decoded("trigger") == timedelta(hours=-24)


def test_categories_include_type_and_tier():
    event = build_event(make_commitment())
    categories = str(event.get("categories").to_ical().decode())
    assert "deadline_on_you" in categories
    assert "CRITICAL" in categories


# --- UID stability --------------------------------------------------------

def test_uid_is_stable_across_regeneration():
    """A republished feed must update the existing event, not duplicate it."""
    commitment = make_commitment()
    assert event_uid(commitment) == event_uid(make_commitment())
    assert event_uid(commitment) != event_uid(make_commitment(id=2))
    assert "@email-commitment-tracker.local" in event_uid(commitment)


def test_uids_are_unique_per_event():
    content = render_ics([make_commitment(), make_commitment(id=2)])
    uids = {str(e.get("uid")) for e in events_of(parse(content))}
    assert len(uids) == 2


# --- Escaping -------------------------------------------------------------

def test_special_characters_are_escaped_and_round_trip():
    """Commas, semicolons and newlines must not corrupt the file."""
    tricky = make_commitment(
        subject="Submit report, slides; and notes",
        evidence_quote="Line one\nLine two, with a comma; and a semicolon",
    )
    content = render_ics([tricky])
    event = events_of(parse(content))[0]

    assert str(event.get("summary")) == "Submit report, slides; and notes"
    assert "Line one\nLine two, with a comma; and a semicolon" in str(
        event.get("description")
    )


def test_unicode_subject_survives_round_trip():
    content = render_ics([make_commitment(subject="Séminaire — 会議 ✓")])
    event = events_of(parse(content))[0]
    assert str(event.get("summary")) == "Séminaire — 会議 ✓"


# --- Publish flow ---------------------------------------------------------

def _store(session, **overrides) -> Commitment:
    email = RawEmail(message_id=f"m{overrides.get('id', 1)}", subject="s")
    session.add(email)
    session.flush()
    commitment = make_commitment(**overrides)
    commitment.id = None
    commitment.email_id = email.id
    session.add(commitment)
    session.flush()
    return commitment


def test_commitments_for_calendar_excludes_undated_and_dismissed(session):
    wanted = _store(session)
    _store(session, id=2, deadline=None)
    _store(session, id=3, status="dismissed")
    session.commit()

    eligible = database.commitments_for_calendar(session)
    assert [c.id for c in eligible] == [wanted.id]


def test_publish_marks_synced_and_writes_the_file(session, tmp_path):
    commitment = _store(session)
    session.commit()
    target = tmp_path / "calendar.ics"

    result = ics_builder.publish_calendar(session, path=target)
    session.commit()

    assert result.events == 1
    assert result.created == 1
    assert result.updated == 0
    assert target.exists()
    assert result.bytes_written > 0

    assert commitment.calendar_synced is True
    assert commitment.ics_uid == event_uid(commitment)
    assert b"BEGIN:VEVENT" in target.read_bytes()


def test_republishing_records_an_update_not_a_duplicate(session, tmp_path):
    _store(session)
    session.commit()
    target = tmp_path / "calendar.ics"

    ics_builder.publish_calendar(session, path=target)
    session.commit()
    second = ics_builder.publish_calendar(session, path=target)
    session.commit()

    assert second.created == 0
    assert second.updated == 1
    # Still exactly one event in the file.
    assert len(events_of(parse(target.read_bytes()))) == 1


def test_publish_writes_a_sync_log_row_per_event(session, tmp_path):
    _store(session)
    _store(session, id=2, deadline=datetime(2026, 9, 1, 9, 0))
    session.commit()

    ics_builder.publish_calendar(session, path=tmp_path / "calendar.ics")
    session.commit()

    entries = database.recent_sync_log(session)
    assert len(entries) == 2
    assert all(e.action == "created" for e in entries)
    assert all(e.status == "success" for e in entries)
    assert all(e.error_message is None for e in entries)


def test_log_sync_can_record_a_failure(session):
    commitment = _store(session)
    session.commit()

    database.log_sync(
        session, commitment.id, action="created", status="failed",
        error_message="disk full",
    )
    session.commit()

    entry = database.recent_sync_log(session)[0]
    assert entry.status == "failed"
    assert entry.error_message == "disk full"
    assert session.query(SyncLog).count() == 1
