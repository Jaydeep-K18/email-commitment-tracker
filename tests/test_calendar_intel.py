"""v2 phase 8: possible duplicates, clashes, and better times to meet."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from src import config
from src.events.recorder import CALENDAR_CONFLICT_DETECTED, CALENDAR_FLAG_RESOLVED
from src.jobs import handlers
from src.jobs.handlers import JobContext, Services
from src.storage.database import session_scope
from src.storage.models import CalendarFlag, Commitment, Event, Job, RawEmail
from src.sync import google_calendar
from src.sync.calendar_intel import (
    WorkingHours,
    commitment_item,
    external_item,
    find_issues,
    free_slots,
    scan,
    upcoming_commitments,
)
from src.sync.google_calendar import APP_TAG_KEY, APP_TAG_VALUE
from tests.test_google_sync import FakeCalendar

MON = datetime(2026, 10, 12)   # a Monday
NOW = MON.replace(hour=8)
HOURS = WorkingHours()


def at(days: int, hour: int, minute: int = 0) -> datetime:
    return MON + timedelta(days=days, hours=hour, minutes=minute)


def item(cid: int, subject: str, when: datetime, type_: str = "meeting", email: int | None = None):
    return commitment_item(Commitment(id=cid, email_id=email if email is not None else 100 + cid,
                                      type=type_, subject=subject, deadline=when))


def google(event_id: str, title: str, start: datetime, end: datetime | None = None, **extra) -> dict:
    end = end or start + timedelta(hours=1)
    return {
        "id": event_id, "summary": title,
        "start": {"dateTime": start.astimezone().isoformat()},
        "end": {"dateTime": end.astimezone().isoformat()},
        "htmlLink": f"https://calendar.google.com/event?eid={event_id}",
        **extra,
    }


def commitment(subject: str, when: datetime, type_: str = "meeting", **fields) -> int:
    with session_scope() as s:
        email = RawEmail(message_id=f"m-{subject}-{when}", subject=subject)
        s.add(email)
        s.flush()
        row = Commitment(email_id=email.id, type=type_, subject=subject, deadline=when,
                         evidence_quote="q", vip_tier=fields.pop("vip_tier", "CRITICAL"), **fields)
        s.add(row)
        s.flush()
        return row.id


def run_scan(external=(), *, checked=True, now=NOW):
    with session_scope() as s:
        return scan(s, list(external), HOURS, now, external_checked=checked)


def flags() -> list[CalendarFlag]:
    with session_scope() as s:
        rows = s.scalars(select(CalendarFlag).order_by(CalendarFlag.id)).all()
        s.expunge_all()
        return rows


# --- The user's own calendar ---------------------------------------------------------

def test_the_users_own_events_count_but_ours_cancelled_and_declined_ones_do_not():
    assert external_item(google("a", "Lunch", at(0, 12))).busy is True
    ours = google("b", "x", at(0, 12), extendedProperties={"private": {APP_TAG_KEY: APP_TAG_VALUE}})
    assert external_item(ours) is None
    assert external_item(google("c", "x", at(0, 12), status="cancelled")) is None
    declined = google("d", "x", at(0, 12), attendees=[{"self": True, "responseStatus": "declined"}])
    assert external_item(declined) is None
    assert external_item(google("e", "Focus", at(0, 12), transparency="transparent")).busy is False
    holiday = external_item({"id": "f", "summary": "Holiday", "start": {"date": "2026-10-12"}, "end": {"date": "2026-10-13"}})
    assert (holiday.all_day, holiday.busy, holiday.start, holiday.end) == (True, False, MON, at(1, 0))


def test_google_times_are_read_on_this_machines_wall_clock():
    event = {"id": "u", "summary": "Call", "start": {"dateTime": "2026-10-12T09:00:00Z"},
             "end": {"dateTime": "2026-10-12T10:00:00Z"}}
    expected = datetime(2026, 10, 12, 9, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert external_item(event).start == expected


# --- Possible duplicates ---------------------------------------------------------------

def test_a_forwarded_request_is_a_possible_duplicate():
    """Different sender, so the resolver will not merge it by itself — ask."""
    first = item(1, "Q3 budget report", at(4, 12), "deadline_on_you")
    forwarded = item(2, "Send the Q3 budget report", at(4, 17), "deadline_on_you")
    [finding] = find_issues([first, forwarded], [], HOURS, NOW)
    assert (finding.kind, finding.commitment_id, finding.other_commitment_id) == ("duplicate", 2, 1)
    assert finding.details["similarity"] == 0.75


def test_similar_but_separate_obligations_are_left_alone():
    report = item(1, "Q3 budget report", at(1, 12), "deadline_on_you")
    assert find_issues([report, item(2, "Q3 budget report", at(4, 12), "deadline_on_you")], [], HOURS, NOW) == []
    assert find_issues([report, item(2, "Expense report", at(1, 12), "deadline_on_you")], [], HOURS, NOW) == []
    same_email = item(2, "Q3 budget report draft", at(1, 12), "deadline_on_you", email=report.email_id)
    assert find_issues([report, same_email], [], HOURS, NOW) == []


def test_a_meeting_already_on_the_calendar_is_a_duplicate_not_a_clash():
    ours = item(1, "Design review with Priya", at(1, 14))
    invite = external_item(google("inv", "Design review", at(1, 14)))
    [finding] = find_issues([ours], [invite], HOURS, NOW)
    assert (finding.kind, finding.external_id) == ("duplicate", "inv")
    assert finding.details["items"][1]["link"].endswith("eid=inv")


# --- Clashes ------------------------------------------------------------------------------

def test_two_meetings_at_once_clash_and_the_later_email_is_the_one_to_move():
    [finding] = find_issues([item(1, "Budget sync", at(1, 10)), item(2, "Vendor call", at(1, 10, 30))], [], HOURS, NOW)
    assert (finding.kind, finding.commitment_id, finding.other_commitment_id) == ("conflict", 2, 1)
    assert finding.details["overlap"] == {"start": "2026-10-13T10:30:00", "end": "2026-10-13T11:00:00"}


def test_a_meeting_over_something_busy_on_the_calendar_clashes():
    [finding] = find_issues([item(1, "Budget sync", at(1, 10))], [external_item(google("x", "Dentist", at(1, 10)))], HOURS, NOW)
    assert (finding.kind, finding.commitment_id, finding.external_id) == ("conflict", 1, "x")


def test_free_time_deadlines_and_back_to_back_meetings_do_not_clash():
    meeting = item(1, "Budget sync", at(1, 10))
    next_one = item(2, "Vendor call", at(1, 11))
    deadline = item(3, "Submit expenses", at(1, 10, 15), "deadline_on_you")
    focus = external_item(google("f", "Focus time", at(1, 10), transparency="transparent"))
    assert find_issues([meeting, next_one, deadline], [focus], HOURS, NOW) == []


# --- Better times ------------------------------------------------------------------------

def test_suggestions_are_free_working_hours_nearest_the_original_time():
    clash = item(2, "Vendor call", at(1, 10, 30))
    busy = [item(1, "Budget sync", at(1, 10)), external_item(google("s", "Standup", at(1, 11, 30))), clash]
    assert free_slots(clash, busy, HOURS, NOW) == [
        {"start": "2026-10-13T09:00:00", "end": "2026-10-13T10:00:00"},
        {"start": "2026-10-13T12:30:00", "end": "2026-10-13T13:30:00"},
        {"start": "2026-10-14T10:30:00", "end": "2026-10-14T11:30:00"},
    ]


def test_suggestions_skip_the_past_and_days_off():
    late = item(2, "Late call", datetime(2026, 10, 16, 17))       # a Friday, 17:00
    other = item(1, "Board prep", datetime(2026, 10, 16, 17))
    slots = free_slots(late, [late, other], HOURS, datetime(2026, 10, 16, 16, 10))
    assert [s["start"] for s in slots] == ["2026-10-19T17:00:00", "2026-10-19T16:30:00", "2026-10-20T17:00:00"]


def test_working_hours_come_from_the_settings_page():
    weekend = WorkingHours.from_settings({"workingHours": {"start": "10:00", "end": "16:00", "days": [0, 6]}})
    assert weekend.is_working_day(date(2026, 10, 18))           # Sunday is 0, as JavaScript counts
    assert not weekend.is_working_day(date(2026, 10, 12))
    assert WorkingHours.from_settings({"workingHours": {"start": "late"}}) == WorkingHours()


# --- Flags: opened once, cleared when solved, dismissed for good ----------------------------

def test_only_upcoming_commitments_headed_for_the_calendar_are_checked():
    upcoming = commitment("Budget sync", at(1, 10))
    commitment("Old sync", at(-3, 10))
    commitment("Newsletter webinar", at(1, 10), vip_tier="SKIP")
    commitment("Any thoughts?", at(1, 10), "question_pending")
    commitment("Cancelled sync", at(1, 10), status="dismissed")
    with session_scope() as s:
        assert [c.id for c in upcoming_commitments(s, NOW)] == [upcoming]


def test_a_clash_opens_one_flag_and_says_so_once():
    first = commitment("Budget sync", at(1, 10))
    second = commitment("Vendor call", at(1, 10, 30))

    assert run_scan().new == 1
    assert run_scan().new == 0

    [flag] = flags()
    assert (flag.kind, flag.status, flag.commitment_id, flag.other_commitment_id) == ("conflict", "open", second, first)
    assert len(flag.details["suggestions"]) == 3
    with session_scope() as s:
        [event] = s.scalars(select(Event).where(Event.type == CALENDAR_CONFLICT_DETECTED)).all()
        assert (event.severity, event.entity_type, event.entity_id) == ("warning", "calendar_flag", str(flag.id))
        assert "Vendor call overlaps Budget sync" in event.message


def test_a_flag_clears_itself_when_the_problem_goes_and_returns_if_it_does():
    commitment("Budget sync", at(1, 10))
    second = commitment("Vendor call", at(1, 10, 30))
    run_scan()

    with session_scope() as s:
        s.get(Commitment, second).status = "dismissed"
    assert run_scan().cleared == 1
    assert flags()[0].status == "resolved"
    with session_scope() as s:
        assert s.scalar(select(Event.payload).where(Event.type == CALENDAR_FLAG_RESOLVED))["how"] == "cleared"

    with session_scope() as s:
        s.get(Commitment, second).status = "pending"
    assert run_scan().new == 1
    assert flags()[0].status == "open"


def test_a_dismissed_flag_stays_dismissed():
    commitment("Budget sync", at(1, 10))
    commitment("Vendor call", at(1, 10, 30))
    run_scan()
    with session_scope() as s:
        s.scalar(select(CalendarFlag)).status = "dismissed"
    assert run_scan().new == 0
    assert flags()[0].status == "dismissed"


def test_flags_about_google_events_wait_until_google_can_be_read():
    commitment("Budget sync", at(1, 10))
    lunch = google("lunch", "Team lunch", at(1, 10))
    run_scan([lunch])
    run_scan([], checked=False)
    assert flags()[0].status == "open"
    run_scan([], checked=True)
    assert flags()[0].status == "resolved"


# --- The job -----------------------------------------------------------------------------

def tomorrow_at(hour: int) -> datetime:
    return (datetime.now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)


def google_ctx(service, available=True) -> JobContext:
    return JobContext(1, 1, 5, Services(
        fetch=lambda: SimpleNamespace(fetched=0, new=0), sync_sent=lambda: 0,
        google_service=lambda: service, google_available=lambda: available,
    ))


def test_the_job_checks_against_the_users_google_calendar():
    calendar = FakeCalendar()
    calendar.user_events = [google("lunch", "Team lunch", tomorrow_at(12))]
    commitment("Budget sync", tomorrow_at(12))

    assert handlers.scan_calendar({}, google_ctx(calendar)) == {"found": 1, "new": 1, "cleared": 0, "google": "checked"}
    assert flags()[0].external_event_id == "lunch"


def test_without_google_the_commitments_are_still_checked_against_each_other():
    commitment("Budget sync", tomorrow_at(12))
    commitment("Vendor call", tomorrow_at(12))
    result = handlers.scan_calendar({}, google_ctx(None, available=False))
    assert (result["found"], result["google"]) == (1, "not connected")


def test_a_google_failure_does_not_stop_the_check():
    class Offline:
        def events(self):
            raise ConnectionError("no route to host")

    commitment("Budget sync", tomorrow_at(12))
    commitment("Vendor call", tomorrow_at(12))
    result = handlers.scan_calendar({}, google_ctx(Offline()))
    assert result["found"] == 1
    assert result["google"] == "unavailable: no route to host"


def test_listing_reads_every_page_of_both_calendars(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "work@group.calendar.google.com")
    calendar = FakeCalendar()
    calendar.user_events = [google(f"e{i}", "x", at(1, 9 + i)) for i in range(5)]

    events = google_calendar.list_user_events(calendar, at(0, 0), at(30, 0))

    assert sorted(e["id"] for e in events) == ["e0", "e1", "e2", "e3", "e4"]
    assert calendar.listed == [("primary", None), ("primary", "2"), ("primary", "4"),
                               ("work@group.calendar.google.com", None),
                               ("work@group.calendar.google.com", "2"),
                               ("work@group.calendar.google.com", "4")]


def test_publishing_the_calendar_queues_a_clash_check():
    handlers.publish_calendar({}, google_ctx(None, available=False))
    with session_scope() as s:
        assert "scan_calendar" in set(s.scalars(select(Job.type)))
