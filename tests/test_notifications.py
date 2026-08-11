"""Tests for new-VIP-email notifications (Phase 6).

Everything the notification does is derived from database state — the scheduler
never pushes anything, because it runs on a background thread that cannot touch
Streamlit. So the whole feature is testable here without a browser: the tier
rule, the seen/unseen bookkeeping, the one-off migration of an existing
database, and the wording of the summary line.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from dashboard import data
from src.collection.email_parser import ParsedEmail
from src.filtering import vip_filter
from src.storage import database
from src.storage.models import Base, RawEmail

NOW = datetime(2026, 8, 11, 9, 0)


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


_seq = iter(range(1, 10_000))


def arrive(session, *, tier="CRITICAL", name="Alice Chen", minutes_ago=0, **overrides):
    """Store an email the way the fetcher does, so tier policy really applies."""
    index = next(_seq)
    parsed = ParsedEmail(
        message_id=f"msg-{index}@example.com",
        thread_id=None,
        sender_name=name,
        sender_email=overrides.pop("sender_email", "alice@university.edu"),
        recipient_email="me@example.com",
        subject=overrides.pop("subject", "Project update"),
        body_text="Please send the report by Friday.",
        received_at=NOW - timedelta(minutes=minutes_ago),
    )
    email = database.save_email(session, parsed, vip_tier=tier, **overrides)
    session.flush()
    return email


# --- Which tiers count as news ---------------------------------------------

@pytest.mark.parametrize("tier", ["CRITICAL", "IMPORTANT", "MONITOR"])
def test_every_vip_tier_raises_a_notification(session, tier):
    arrive(session, tier=tier)
    assert database.count_unseen_vip_emails(session) == 1


def test_skip_tier_email_never_notifies(session):
    """The whole point of SKIP is that the user does not want to hear about it."""
    arrive(session, tier="SKIP", name="Newsletter")
    assert database.unseen_vip_emails(session) == []


def test_untagged_email_never_notifies(session):
    """A sender the VIP filter has not classified is not yet *known* to be a VIP."""
    arrive(session, tier=None, name="Stranger")
    assert database.unseen_vip_emails(session) == []


def test_notification_is_independent_of_extraction(session):
    """An email is news when it lands, not when the LLM gets round to it.

    ``processed`` is set for very different reasons (SKIP senders, a completed
    extraction, a re-queue), so coupling the two would make notifications
    reappear or vanish for no reason the user could see.
    """
    arrive(session, processed=True)
    arrive(session, processed=False)
    assert database.count_unseen_vip_emails(session) == 2


def test_unseen_emails_come_back_newest_first(session):
    arrive(session, name="Oldest", minutes_ago=90)
    arrive(session, name="Newest", minutes_ago=1)
    arrive(session, name="Middle", minutes_ago=30)

    names = [e.sender_name for e in database.unseen_vip_emails(session)]
    assert names == ["Newest", "Middle", "Oldest"]


# --- Marking seen -----------------------------------------------------------

def test_marking_seen_clears_the_notification(session):
    arrive(session)
    arrive(session, tier="IMPORTANT")

    assert database.mark_emails_seen(session) == 2
    assert database.count_unseen_vip_emails(session) == 0


def test_marking_seen_twice_is_idempotent(session):
    """Streamlit reruns make double submissions easy; the second must be a no-op."""
    arrive(session)
    assert database.mark_emails_seen(session) == 1
    assert database.mark_emails_seen(session) == 0


def test_marking_seen_does_not_disturb_anything_else(session):
    email = arrive(session, processed=True)
    database.mark_emails_seen(session)
    assert email.processed is True
    assert email.vip_tier == "CRITICAL"


def test_marking_specific_ids_leaves_later_arrivals_unseen(session):
    """An email that lands between rendering and clicking must not be swallowed."""
    shown = arrive(session, name="Alice Chen")
    later = arrive(session, name="Bob Rao")

    assert database.mark_emails_seen(session, [shown.id]) == 1

    remaining = database.unseen_vip_emails(session)
    assert [e.id for e in remaining] == [later.id]


def test_marking_an_empty_id_list_changes_nothing(session):
    arrive(session)
    assert database.mark_emails_seen(session, []) == 0
    assert database.count_unseen_vip_emails(session) == 1


# --- No retroactive flood ---------------------------------------------------

def _legacy_database(tmp_path):
    """A database file whose ``raw_emails`` predates the notification column."""
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE raw_emails ("
                " id INTEGER PRIMARY KEY,"
                " message_id VARCHAR NOT NULL UNIQUE,"
                " thread_id VARCHAR,"
                " sender_email VARCHAR,"
                " sender_name VARCHAR,"
                " recipient_email VARCHAR,"
                " subject TEXT,"
                " body_text TEXT,"
                " received_at DATETIME,"
                " vip_tier VARCHAR,"
                " processed BOOLEAN NOT NULL,"
                " fetched_at DATETIME NOT NULL)"
            )
        )
        for index in range(12):
            connection.execute(
                text(
                    "INSERT INTO raw_emails"
                    " (message_id, sender_email, sender_name, vip_tier, processed,"
                    "  fetched_at)"
                    " VALUES (:mid, 'alice@university.edu', 'Alice Chen',"
                    "         'CRITICAL', 1, :fetched)"
                ),
                {"mid": f"old-{index}@example.com", "fetched": NOW.isoformat(" ")},
            )
    return engine


@pytest.fixture()
def legacy_engine(tmp_path, monkeypatch):
    """Point the storage layer at a pre-notification database file."""
    engine = _legacy_database(tmp_path)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(
        database,
        "SessionLocal",
        sessionmaker(bind=engine, future=True, expire_on_commit=False),
    )
    try:
        yield engine
    finally:
        engine.dispose()


def test_migrating_an_existing_database_announces_nothing(legacy_engine):
    """The upgrade must not announce twelve emails the user read days ago.

    Everything already stored arrived before the feature existed, so the
    migration backfills it as already-seen. Getting this wrong is not a cosmetic
    bug: a toast listing a dozen stale senders is exactly the noise that teaches
    a user to ignore notifications.
    """
    database.init_db()

    with database.session_scope() as session:
        stored = session.execute(select(func.count()).select_from(RawEmail))
        assert stored.scalar_one() == 12  # nothing lost
        assert database.count_unseen_vip_emails(session) == 0


def test_email_arriving_after_the_migration_does_notify(legacy_engine):
    database.init_db()

    with database.session_scope() as session:
        arrive(session, name="Bob Rao")

    with database.session_scope() as session:
        notice = data.new_email_notice(session)
    assert notice.count == 1
    assert notice.senders == ["Bob Rao"]


def test_a_second_migration_run_does_not_swallow_pending_email(legacy_engine):
    """``init_db`` runs on every dashboard start and every scheduler cycle.

    If the backfill re-ran it would silently swallow genuinely new email, so it
    must be tied to the column's creation rather than to startup.
    """
    database.init_db()
    with database.session_scope() as session:
        arrive(session, name="Bob Rao")

    database.init_db()  # as a later start would

    with database.session_scope() as session:
        assert database.count_unseen_vip_emails(session) == 1


def test_a_fresh_database_starts_with_nothing_pending(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}", future=True)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(
        database,
        "SessionLocal",
        sessionmaker(bind=engine, future=True, expire_on_commit=False),
    )
    try:
        database.init_db()
        with database.session_scope() as session:
            assert database.count_unseen_vip_emails(session) == 0
    finally:
        engine.dispose()


def test_promoting_an_old_skip_sender_does_not_announce_their_history(session):
    """Re-tagging is the same trap as the migration, reached a different way.

    Adding a VIP rule re-tags stored email (``apply_tiers_to_stored_emails``).
    Those emails were stored already-seen, so promoting the sender surfaces
    their *next* email rather than their back-catalogue.
    """
    arrive(session, tier="SKIP", name="Carla Diaz", sender_email="carla@lab.org")
    database.add_vip_contact(session, "carla@lab.org", "exact_email", "CRITICAL")
    vip_filter.apply_tiers_to_stored_emails(session, retag_all=True)

    assert database.count_unseen_vip_emails(session) == 0


# --- The summary line -------------------------------------------------------

def test_one_sender_reads_in_the_singular(session):
    arrive(session, name="Alice Chen")
    assert data.new_email_notice(session).message == (
        "📬 1 new email from Alice Chen"
    )


def test_two_senders_are_both_named(session):
    arrive(session, name="Bob Rao", minutes_ago=10)
    arrive(session, name="Alice Chen", minutes_ago=1)
    assert data.new_email_notice(session).message == (
        "📬 2 new emails from Alice Chen, Bob Rao"
    )


def test_many_senders_are_truncated_with_a_count(session):
    for index, name in enumerate(["Alice", "Bob", "Carla", "Dan", "Eve"]):
        arrive(session, name=name, minutes_ago=index)  # Alice wrote most recently

    notice = data.new_email_notice(session)
    # Newest first, so the people who wrote longest ago are the ones collapsed.
    assert notice.message == "📬 5 new emails from Alice, Bob, Carla and 2 others"


def test_a_single_hidden_sender_is_singular():
    assert data.summarise_senders(["A", "B", "C", "D"]) == "A, B, C and 1 other"


def test_repeat_emails_from_one_person_are_named_once(session):
    """Three emails, one name — otherwise a chatty sender fills the whole toast."""
    for _ in range(3):
        arrive(session, name="Alice Chen")

    notice = data.new_email_notice(session)
    assert notice.count == 3
    assert notice.senders == ["Alice Chen"]
    assert notice.message == "📬 3 new emails from Alice Chen"


def test_a_sender_with_no_display_name_falls_back_to_the_address(session):
    arrive(session, name=None, sender_email="quiet@lab.org")
    assert data.new_email_notice(session).message == (
        "📬 1 new email from quiet@lab.org"
    )


def test_a_sender_with_no_name_or_address_still_reads_sensibly(session):
    arrive(session, name=None, sender_email=None)
    assert "unknown sender" in data.new_email_notice(session).message


def test_an_empty_notice_is_falsey_and_says_nothing(session):
    notice = data.new_email_notice(session)
    assert not notice
    assert notice.count == 0
    assert notice.senders == []
    assert notice.email_ids == []


def test_summarising_no_senders_is_empty():
    assert data.summarise_senders([]) == ""


def test_the_notice_carries_the_ids_it_covers(session):
    first = arrive(session, minutes_ago=5)
    second = arrive(session, minutes_ago=1)

    notice = data.new_email_notice(session)
    assert set(notice.email_ids) == {first.id, second.id}
    assert data.unseen_email_count(session) == 2


# --- The Streamlit layer ----------------------------------------------------
#
# ``AppTest`` runs the real script and the real widgets in-process, so these
# cover the parts ``dashboard/notifications.py`` adds on top of the logic above:
# the once-per-session toast, and the badge clearing when dismissed.

#: How ``dashboard/app.py`` is expected to wire the feature in.
_WIRING = """
import streamlit as st
from dashboard import notifications

notice = notifications.pending_notice()
notifications.render_toast(notice)
with st.sidebar:
    notifications.render_sidebar_badge(notice)
"""


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """An AppTest wired to a throwaway database instead of the user's real one."""
    from streamlit.testing.v1 import AppTest

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)

    db = TestSession()
    arrive(db, name="Alice Chen")
    db.commit()
    db.close()
    try:
        yield AppTest.from_string(_WIRING)
    finally:
        engine.dispose()


def test_an_arrival_is_toasted_on_the_next_dashboard_rerun(app):
    app.run()
    assert [t.value for t in app.toast] == ["📬 1 new email from Alice Chen"]


def test_the_same_arrival_is_not_toasted_again_on_every_rerun(app):
    """Streamlit reruns on every click; the toast must not follow the user around."""
    app.run()
    app.run()
    assert app.toast == []
    # The badge stays, though — it is the thing that persists until dismissed.
    assert any("Mark as read" in b.label for b in app.sidebar.button)


def test_dismissing_the_badge_clears_it(app):
    app.run()
    app.sidebar.button[0].click().run()

    # The click has already written the change; a real browser would replay the
    # script for the ``st.rerun()`` the dismiss asks for, which AppTest leaves
    # to the caller.
    app.run()

    assert app.sidebar.button == []  # nothing left to dismiss
    assert app.toast == []
