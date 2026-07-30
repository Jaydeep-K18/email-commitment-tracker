"""Build a standard ``.ics`` calendar from commitment records.

This is the only part of the system whose output leaves the machine, and even
then only to the user's own calendar app (PROJECT_PLAN.md §16). Each event
carries a title, a date, a reminder, and the evidence quote that justified it —
nothing else from the email.

**Timezone handling.** Deadlines are stored as the wall-clock time written in the
email (see Phase 3 notes), so events are emitted as RFC 5545 *floating* times —
no ``Z``, no ``TZID``. A floating time means "this local time, wherever you are",
which is exactly what "the report is due at 5pm" means to a person. Converting to
UTC here would make a 10:00 IST deadline display as 04:30.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta, timezone

from icalendar import Alarm, Calendar, Event
from icalendar.prop import vDuration

from src import config
from src.storage.models import Commitment

#: Namespace for event UIDs. A commitment always maps to the same UID, so a
#: republished feed updates the existing event instead of creating a duplicate.
UID_DOMAIN = "email-commitment-tracker.local"

PRODID = "-//Email Commitment Tracker//EN"

#: Titles get a short prefix where the type is not obvious from the text alone.
_TITLE_PREFIX = {
    "deadline_on_you": "",
    "deadline_from_others": "Waiting: ",
    "meeting": "",
    "question_pending": "Reply: ",
}

_TYPE_LABEL = {
    "deadline_on_you": "Your deadline",
    "deadline_from_others": "Owed to you",
    "meeting": "Meeting",
    "question_pending": "Question awaiting your reply",
}


def event_uid(commitment: Commitment) -> str:
    """Stable UID for a commitment's calendar event.

    An already-assigned ``ics_uid`` always wins. That is what lets a follow-up
    email update an existing event: the conflict resolver hands the newer
    commitment the UID of the one it replaces, so the subscriber's calendar
    revises the event in place instead of gaining a second copy (Phase 5).
    """
    if commitment.ics_uid:
        return commitment.ics_uid
    if commitment.id is not None:
        return f"commitment-{commitment.id}@{UID_DOMAIN}"
    # Fall back to a content hash for unsaved commitments (used in tests).
    digest = hashlib.sha1(
        f"{commitment.subject}|{commitment.deadline}".encode()
    ).hexdigest()[:16]
    return f"commitment-{digest}@{UID_DOMAIN}"


def is_all_day(deadline: datetime) -> bool:
    """A deadline with no time component is shown as an all-day event."""
    return deadline.time() == time(0, 0)


def build_summary(commitment: Commitment) -> str:
    prefix = _TITLE_PREFIX.get(commitment.type, "")
    subject = (commitment.subject or "Commitment").strip()
    if prefix == "Waiting: " and commitment.counterparty_name:
        # "Waiting: Ravi — design files" reads better than a bare title.
        return f"Waiting: {commitment.counterparty_name} — {subject}"
    return f"{prefix}{subject}"


def build_description(commitment: Commitment) -> str:
    """Human-readable body carrying the traceability the plan requires."""
    lines: list[str] = []
    label = _TYPE_LABEL.get(commitment.type, commitment.type)
    lines.append(label)
    lines.append("")

    if commitment.evidence_quote:
        lines.append("From the email:")
        lines.append(f'"{commitment.evidence_quote.strip()}"')
        lines.append("")

    who = commitment.counterparty_name or ""
    if commitment.counterparty_email:
        who = f"{who} <{commitment.counterparty_email}>".strip()
    if who:
        lines.append(f"Contact: {who}")
    if commitment.vip_tier:
        lines.append(f"Priority: {commitment.vip_tier}")
    lines.append(f"Confidence: {commitment.confidence:.0%}")
    if commitment.email_id:
        lines.append(f"Source email: #{commitment.email_id}")

    lines.append("")
    lines.append("Extracted locally by Email Commitment Tracker.")
    return "\n".join(lines)


def _build_alarm(all_day: bool) -> Alarm:
    """A display reminder ahead of the deadline."""
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    if all_day:
        trigger = timedelta(hours=-config.CALENDAR_ALLDAY_REMINDER_HOURS)
    else:
        trigger = timedelta(minutes=-config.CALENDAR_REMINDER_MINUTES)
    alarm.add("trigger", trigger)
    alarm.add("description", "Email Commitment Tracker reminder")
    return alarm


def build_event(commitment: Commitment, now: datetime | None = None) -> Event:
    """Convert one commitment into a VEVENT."""
    if commitment.deadline is None:
        raise ValueError("commitment has no deadline and cannot become an event")

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    event = Event()
    event.add("uid", event_uid(commitment))
    event.add("summary", build_summary(commitment))
    event.add("description", build_description(commitment))
    event.add("dtstamp", now)

    all_day = is_all_day(commitment.deadline)
    if all_day:
        start: date = commitment.deadline.date()
        event.add("dtstart", start)
        # DTEND is exclusive for all-day events.
        event.add("dtend", start + timedelta(days=1))
        event.add("transp", "TRANSPARENT")
    else:
        event.add("dtstart", commitment.deadline)
        event.add("dtend", commitment.deadline + timedelta(hours=1))
        event.add("transp", "OPAQUE")

    categories = [commitment.type]
    if commitment.vip_tier:
        categories.append(commitment.vip_tier)
    event.add("categories", categories)

    if commitment.created_at:
        event.add("created", commitment.created_at)
    event.add("last-modified", now)
    event.add("status", "CONFIRMED")
    event.add_component(_build_alarm(all_day))
    return event


def build_calendar(
    commitments: list[Commitment], now: datetime | None = None
) -> Calendar:
    """Assemble a full VCALENDAR from commitments that have a deadline."""
    calendar = Calendar()
    calendar.add("prodid", PRODID)
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    # Non-standard but widely honoured hints for subscription feeds.
    calendar.add("x-wr-calname", config.CALENDAR_NAME)
    calendar.add("x-wr-caldesc", "Deadlines extracted from your email, locally.")
    # Both must be typed as RFC 5545 DURATION (e.g. PT1H). Passing a bare
    # timedelta renders Python's "1:00:00", which makes the file unparseable.
    refresh = vDuration(timedelta(minutes=config.CALENDAR_REFRESH_MINUTES))
    calendar.add("refresh-interval", refresh, parameters={"VALUE": "DURATION"})
    calendar.add("x-published-ttl", refresh)

    for commitment in commitments:
        if commitment.deadline is None:
            continue
        calendar.add_component(build_event(commitment, now=now))
    return calendar


def render_ics(commitments: list[Commitment], now: datetime | None = None) -> bytes:
    """Render commitments to ``.ics`` bytes ready to serve or write."""
    return build_calendar(commitments, now=now).to_ical()


def write_ics_file(commitments: list[Commitment], path=None) -> int:
    """Write the calendar to disk, returning the number of bytes written."""
    target = path or config.ICS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    content = render_ics(commitments)
    target.write_bytes(content)
    return len(content)


# Publishing is orchestrated by :func:`src.sync.sync_engine.run_sync`, which
# owns tier policy and conflict resolution. This module stays pure rendering so
# there is exactly one code path deciding what reaches the calendar.
