"""Tests for the dashboard's data layer (Phase 6).

The dashboard's logic lives in ``dashboard/data.py`` precisely so it can be
tested here without Streamlit or a browser.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dashboard import data
from src.storage.models import Base

from tests.test_sync_engine import store  # shared builder


NOW = datetime(2026, 7, 30, 17, 0)  # a Thursday afternoon


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


# --- Urgency ---------------------------------------------------------------

def test_urgency_bands():
    assert data.urgency(NOW - timedelta(days=1), now=NOW) == data.OVERDUE
    assert data.urgency(NOW, now=NOW) == data.TODAY
    assert data.urgency(NOW + timedelta(days=2), now=NOW) == data.URGENT
    assert data.urgency(NOW + timedelta(days=30), now=NOW) == data.UPCOMING
    assert data.urgency(None, now=NOW) == data.UNDATED


def test_a_deadline_earlier_today_still_reads_as_today():
    """17:00 on the day of a 09:00 deadline: a person still calls that today."""
    this_morning = NOW.replace(hour=9, minute=0)
    assert data.urgency(this_morning, now=NOW) == data.TODAY


def test_a_deadline_just_after_midnight_tomorrow_is_not_overdue():
    tomorrow = (NOW + timedelta(days=1)).replace(hour=0, minute=1)
    assert data.urgency(tomorrow, now=NOW) == data.URGENT


def test_every_band_has_a_colour_and_an_icon():
    for band in (data.OVERDUE, data.TODAY, data.URGENT, data.UPCOMING, data.UNDATED):
        assert band in data.URGENCY_STYLE
        assert band in data.URGENCY_ICON


# --- Formatting ------------------------------------------------------------

def test_midnight_deadline_is_shown_without_a_time():
    assert data.format_deadline(datetime(2026, 8, 4, 0, 0)) == "Tue 04 Aug 2026"


def test_timed_deadline_keeps_its_time():
    assert data.format_deadline(datetime(2026, 8, 4, 14, 30)) == (
        "Tue 04 Aug 2026, 14:30"
    )


def test_missing_deadline_is_labelled():
    assert data.format_deadline(None) == "No deadline"


@pytest.mark.parametrize(
    "delta_days, expected",
    [(0, "today"), (1, "tomorrow"), (3, "in 3 days"),
     (-1, "1 day overdue"), (-4, "4 days overdue")],
)
def test_relative_deadline(delta_days, expected):
    when = NOW + timedelta(days=delta_days)
    assert data.relative_deadline(when, now=NOW) == expected


# --- Snapshot --------------------------------------------------------------

def test_snapshot_counts_the_things_the_overview_shows(session):
    store(session, deadline=NOW - timedelta(days=2))                    # overdue
    store(session, deadline=NOW)                                        # today
    store(session, deadline=NOW + timedelta(days=2))                    # this week
    store(session, deadline=NOW + timedelta(days=40))                   # later
    store(session, type="question_pending", deadline=None)              # question
    store(session, type="deadline_from_others",
          deadline=NOW + timedelta(days=5))                             # owed to you
    session.commit()

    snapshot = data.build_snapshot(session, now=NOW)

    assert snapshot.total_open == 6
    assert snapshot.overdue == 1
    assert snapshot.due_today == 1
    assert snapshot.due_this_week == 3   # today, +2, +5
    assert snapshot.questions_pending == 1
    assert snapshot.you_owe == 4
    assert snapshot.owed_to_you == 1


def test_snapshot_excludes_closed_commitments(session):
    store(session)
    store(session, status="dismissed")
    store(session, status="superseded")
    store(session, status="fulfilled")
    session.commit()

    assert data.build_snapshot(session, now=NOW).total_open == 1


def test_snapshot_separates_published_from_awaiting_approval(session):
    store(session, vip_tier="CRITICAL")
    store(session, vip_tier="MONITOR")
    store(session, vip_tier="SKIP")
    session.commit()

    snapshot = data.build_snapshot(session, now=NOW)
    assert snapshot.on_calendar == 1
    assert snapshot.awaiting_approval == 1


def test_snapshot_of_an_empty_database(session):
    snapshot = data.build_snapshot(session, now=NOW)
    assert snapshot.total_open == 0
    assert snapshot.on_calendar == 0


# --- Filters ---------------------------------------------------------------

def test_filtering_by_type(session):
    store(session, type="meeting")
    store(session, type="deadline_on_you")
    session.commit()
    rows = data.feed_commitments(session, types=["meeting"])
    assert [c.type for c in rows] == ["meeting"]


def test_filtering_by_tier_includes_untiered(session):
    store(session, vip_tier=None)
    store(session, vip_tier="CRITICAL")
    session.commit()
    rows = data.feed_commitments(session, tiers=["untiered"])
    assert [c.vip_tier for c in rows] == [None]


def test_filtering_by_urgency(session):
    store(session, deadline=NOW - timedelta(days=1))
    store(session, deadline=NOW + timedelta(days=20))
    session.commit()
    rows = data.feed_commitments(session, urgencies=[data.OVERDUE], now=NOW)
    assert len(rows) == 1


def test_search_matches_subject_person_and_evidence(session):
    store(session, subject="Renew the office domain")
    store(session, subject="Sprint planning", counterparty_name="Priya Sharma")
    store(session, subject="Send invoice", evidence_quote="please send the invoice")
    session.commit()

    assert len(data.feed_commitments(session, search="domain")) == 1
    assert len(data.feed_commitments(session, search="priya")) == 1
    assert len(data.feed_commitments(session, search="invoice")) == 1
    assert len(data.feed_commitments(session, search="nothing here")) == 0


def test_search_is_case_insensitive(session):
    store(session, subject="Renew the Office Domain")
    session.commit()
    assert len(data.feed_commitments(session, search="OFFICE")) == 1


def test_filters_combine(session):
    store(session, type="meeting", vip_tier="CRITICAL")
    store(session, type="meeting", vip_tier="MONITOR")
    store(session, type="deadline_on_you", vip_tier="CRITICAL")
    session.commit()

    rows = data.feed_commitments(session, types=["meeting"], tiers=["CRITICAL"])
    assert len(rows) == 1


def test_no_filters_returns_everything_open(session):
    store(session)
    store(session)
    session.commit()
    assert len(data.feed_commitments(session)) == 2


def test_closed_commitments_are_hidden_unless_asked_for(session):
    store(session)
    store(session, status="superseded")
    session.commit()

    assert len(data.feed_commitments(session)) == 1
    assert len(data.feed_commitments(session, include_closed=True)) == 2


# --- Ordering --------------------------------------------------------------

def test_feed_is_ordered_soonest_first_with_undated_last(session):
    store(session, subject="later", deadline=NOW + timedelta(days=10))
    store(session, subject="undated", deadline=None)
    store(session, subject="sooner", deadline=NOW + timedelta(days=1))
    session.commit()

    assert [c.subject for c in data.feed_commitments(session)] == [
        "sooner", "later", "undated",
    ]


# --- Questions -------------------------------------------------------------

def test_pending_questions_lists_only_open_questions(session):
    store(session, type="question_pending", subject="open one")
    store(session, type="question_pending", subject="done", status="dismissed")
    store(session, type="deadline_on_you", subject="not a question")
    session.commit()

    assert [c.subject for c in data.pending_questions(session)] == ["open one"]
