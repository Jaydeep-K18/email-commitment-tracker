"""Calendar intelligence: possible duplicates, conflicts, and better times.

The conflict resolver merges the clear cases by itself: same type, same person,
same subject. This finds the cases it must not decide alone, and records them in
calendar_flags for the user to settle:

- **duplicate** — two commitments that look like one obligation but did not meet
  the resolver's bar (a colleague forwarded the request; one reads as a meeting,
  the other as a deadline), or a commitment that is already on the user's own
  Google Calendar, usually as the invite the email was about.
- **conflict** — a meeting that overlaps another meeting, or something busy on
  the user's calendar. It carries free slots in working hours, to propose
  instead.

Flagged, never fixed: merging two different obligations, or moving a meeting,
is worse than asking.

Times are naive wall-clock, as deadlines are stored, in this machine's zone —
the zone Google is told when an event is pushed (google_calendar._local_rfc3339).
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.events.recorder import (
    CALENDAR_CONFLICT_DETECTED,
    CALENDAR_DUPLICATE_DETECTED,
    CALENDAR_FLAG_RESOLVED,
    email_correlation,
    record_event,
)
from src.storage.models import CalendarFlag, Commitment
from src.sync.conflict_resolver import normalize_subject, subject_similarity
from src.sync.ics_builder import is_all_day

log = logging.getLogger(__name__)

#: How far ahead to look. Further out, plans are still moving.
HORIZON_DAYS = 60
#: A timed commitment becomes an event this long (ics_builder, google_calendar).
EVENT_LENGTH = timedelta(hours=1)
#: Subject overlap needed to call two things possibly the same. Lower than the
#: resolver's merge threshold: this only asks.
DUPLICATE_SIMILARITY = 0.5
#: As in the resolver, one shared word is not evidence.
MIN_SHARED_WORDS = 2
#: Two copies of one obligation are rarely more than a day apart.
DUPLICATE_WINDOW = timedelta(days=1)
#: Suggested times: half-hour steps, at most two a day, over this many working days.
SLOT_STEP = timedelta(minutes=30)
SLOTS_PER_DAY = 2
SLOT_LIMIT = 3
SLOT_SEARCH_DAYS = 5


@dataclass(frozen=True)
class Item:
    """One thing on the calendar: one of our commitments, or the user's own event."""

    ref: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    #: Something to attend, rather than a time something is due.
    meeting: bool
    #: Shown as busy. False for "free" events and anything all-day.
    busy: bool
    commitment_id: int | None = None
    email_id: int | None = None
    external_id: str | None = None
    link: str | None = None

    @property
    def ours(self) -> bool:
        return self.commitment_id is not None

    def overlaps(self, other: "Item") -> bool:
        return self.start < other.end and other.start < self.end

    def describe(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "allDay": self.all_day,
            **({"link": self.link} if self.link else {}),
        }


def commitment_item(commitment: Commitment) -> Item | None:
    if commitment.deadline is None:
        return None
    all_day = is_all_day(commitment.deadline)
    start = datetime.combine(commitment.deadline.date(), time()) if all_day else commitment.deadline
    end = start + (timedelta(days=1) if all_day else EVENT_LENGTH)
    meeting = commitment.type == "meeting"
    return Item(
        ref=f"commitment:{commitment.id}",
        title=commitment.subject,
        start=start,
        end=end,
        all_day=all_day,
        meeting=meeting,
        busy=meeting and not all_day,
        commitment_id=commitment.id,
        email_id=commitment.email_id,
    )


def _wall_clock(value: str) -> datetime:
    """An RFC 3339 time from Google as this machine's wall clock."""
    moment = datetime.fromisoformat(value)
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


def external_item(event: dict) -> Item | None:
    """One of the user's own Google events, or None if it does not occupy them.

    Our own events are skipped (they are the commitments, already counted), as
    are cancelled ones and invitations the user declined.
    """
    from src.sync.google_calendar import APP_TAG_KEY, APP_TAG_VALUE

    if event.get("status") == "cancelled":
        return None
    private = (event.get("extendedProperties") or {}).get("private") or {}
    if private.get(APP_TAG_KEY) == APP_TAG_VALUE:
        return None
    if any(a.get("self") and a.get("responseStatus") == "declined" for a in event.get("attendees") or []):
        return None
    start, end = event.get("start") or {}, event.get("end") or {}
    try:
        if "dateTime" in start:
            begins, ends, all_day = _wall_clock(start["dateTime"]), _wall_clock(end["dateTime"]), False
        else:
            begins = datetime.combine(date.fromisoformat(start["date"]), time())
            ends = datetime.combine(date.fromisoformat(end["date"]), time())
            all_day = True
    except (KeyError, ValueError):
        return None
    return Item(
        ref=f"google:{event['id']}",
        title=(event.get("summary") or "").strip() or "(No title)",
        start=begins,
        end=ends,
        all_day=all_day,
        meeting=not all_day,
        busy=not all_day and event.get("transparency") != "transparent",
        external_id=event["id"],
        link=event.get("htmlLink"),
    )


# --- Working hours and free time ------------------------------------------------

@dataclass(frozen=True)
class WorkingHours:
    start: time = time(9)
    end: time = time(18)
    #: JavaScript numbering, as the settings page saves it: 0 is Sunday.
    days: frozenset[int] = frozenset({1, 2, 3, 4, 5})

    @classmethod
    def from_settings(cls, calendar: dict) -> "WorkingHours":
        hours = calendar.get("workingHours") or {}
        try:
            return cls(
                start=time.fromisoformat(hours.get("start", "09:00")),
                end=time.fromisoformat(hours.get("end", "18:00")),
                days=frozenset(int(d) for d in hours.get("days", [1, 2, 3, 4, 5])),
            )
        except (TypeError, ValueError):
            return cls()

    def is_working_day(self, day: date) -> bool:
        return (day.weekday() + 1) % 7 in self.days


def free_slots(
    around: Item, busy: list[Item], hours: WorkingHours, now: datetime, *, length: timedelta = EVENT_LENGTH,
) -> list[dict[str, str]]:
    """Free spans of ``length`` in working hours, to propose instead of ``around``.

    Its own day first, nearest its original time; then the next working days.
    """
    if not hours.days:
        return []
    taken = [b for b in busy if b.ref != around.ref]
    suggestions: list[dict[str, str]] = []
    day, days_seen = around.start.date(), 0
    while len(suggestions) < SLOT_LIMIT and days_seen <= SLOT_SEARCH_DAYS:
        if hours.is_working_day(day):
            opens, closes = datetime.combine(day, hours.start), datetime.combine(day, hours.end)
            candidates = []
            slot = opens
            while slot + length <= closes:
                proposal = Item("slot", "", slot, slot + length, False, True, True)
                if slot >= now and not any(proposal.overlaps(b) for b in taken):
                    candidates.append(slot)
                slot += SLOT_STEP
            wanted = datetime.combine(day, around.start.time())
            candidates.sort(key=lambda s: (abs(s - wanted), s))
            for slot in candidates[:SLOTS_PER_DAY]:
                if len(suggestions) < SLOT_LIMIT:
                    suggestions.append({"start": slot.isoformat(), "end": (slot + length).isoformat()})
            days_seen += 1
        day += timedelta(days=1)
    return suggestions


# --- Finding ----------------------------------------------------------------------

@dataclass
class Finding:
    kind: str
    commitment_id: int
    other_commitment_id: int | None
    external_id: str | None
    details: dict
    email_id: int | None = None
    title: str = ""
    other_title: str = ""

    @property
    def key(self) -> str:
        if self.external_id is not None:
            ext = self.external_id if len(self.external_id) <= 100 else hashlib.sha1(self.external_id.encode()).hexdigest()
            return f"{self.kind}:{self.commitment_id}:google:{ext}"
        low, high = sorted((self.commitment_id, self.other_commitment_id))
        return f"{self.kind}:{low}:{high}"


def _alike(a: Item, b: Item) -> float | None:
    """Subject similarity if the two look like one obligation, else None."""
    if abs(a.start - b.start) > DUPLICATE_WINDOW:
        return None
    if len(normalize_subject(a.title) & normalize_subject(b.title)) < MIN_SHARED_WORDS:
        return None
    score = subject_similarity(a.title, b.title)
    return score if score >= DUPLICATE_SIMILARITY else None


def _pair(kind: str, mine: Item, other: Item, details: dict) -> Finding:
    """A finding about ``mine``, a commitment, and ``other``, either kind."""
    return Finding(
        kind=kind,
        commitment_id=mine.commitment_id,
        other_commitment_id=other.commitment_id,
        external_id=other.external_id,
        details={"items": [mine.describe(), other.describe()], **details},
        email_id=mine.email_id,
        title=mine.title,
        other_title=other.title,
    )


def find_issues(ours: list[Item], external: list[Item], hours: WorkingHours, now: datetime) -> list[Finding]:
    findings: list[Finding] = []
    duplicate_refs: set[frozenset[str]] = set()

    for i, a in enumerate(ours):
        for b in ours[i + 1:]:
            if a.email_id is not None and a.email_id == b.email_id:
                continue   # found side by side in one email: two obligations
            score = _alike(a, b)
            if score is not None:
                newer, older = (a, b) if a.commitment_id > b.commitment_id else (b, a)
                findings.append(_pair("duplicate", newer, older, {"similarity": round(score, 2)}))
                duplicate_refs.add(frozenset((a.ref, b.ref)))
        for event in external:
            score = _alike(a, event)
            if score is not None:
                findings.append(_pair("duplicate", a, event, {"similarity": round(score, 2)}))
                duplicate_refs.add(frozenset((a.ref, event.ref)))

    busy = [item for item in ours + external if item.busy]
    for i, a in enumerate(ours):
        if not a.busy:
            continue
        for b in ours[i + 1:] + external:
            if not b.busy or not a.overlaps(b) or frozenset((a.ref, b.ref)) in duplicate_refs:
                continue
            # The later email's meeting is the one to propose moving.
            mine, other = (a, b) if not b.ours or a.commitment_id > b.commitment_id else (b, a)
            overlap = {"start": max(a.start, b.start).isoformat(), "end": min(a.end, b.end).isoformat()}
            findings.append(_pair("conflict", mine, other, {
                "overlap": overlap,
                "suggestions": free_slots(mine, busy, hours, now),
            }))
    return findings


# --- Recording -------------------------------------------------------------------

@dataclass
class ScanResult:
    found: int = 0
    new: int = 0
    cleared: int = 0
    flag_ids: list[int] = field(default_factory=list)


def upcoming_commitments(session: Session, now: datetime) -> list[Commitment]:
    """Open commitments headed for the calendar — or waiting for approval to be."""
    from src.sync.sync_engine import decide

    rows = session.scalars(
        select(Commitment).where(
            Commitment.deadline.is_not(None),
            Commitment.deadline >= datetime.combine(now.date(), time()),
            Commitment.deadline < now + timedelta(days=HORIZON_DAYS),
            Commitment.status.in_(("pending", "overdue")),
        ).order_by(Commitment.id)
    )
    return [c for c in rows if (d := decide(c)).should_sync or d.awaiting_approval]


def _announce(session: Session, flag: CalendarFlag, finding: Finding) -> None:
    when = finding.details["items"][0]["start"][:10]
    if finding.kind == "conflict":
        event_type, message = CALENDAR_CONFLICT_DETECTED, f"Clash on {when}: {finding.title} overlaps {finding.other_title}"
    else:
        where = "your calendar" if finding.external_id else "another commitment"
        event_type, message = CALENDAR_DUPLICATE_DETECTED, f"Possible duplicate: {finding.title} looks like {finding.other_title} on {where}"
    record_event(
        session, event_type, message,
        entity_type="calendar_flag", entity_id=flag.id,
        correlation_id=email_correlation(finding.email_id),
        severity="warning",
        payload={"kind": finding.kind, "commitment_id": finding.commitment_id,
                 "other_commitment_id": finding.other_commitment_id, "external": finding.external_id is not None},
    )


def record(session: Session, findings: list[Finding], now: datetime, *, external_checked: bool) -> ScanResult:
    """Bring calendar_flags in line with what was found.

    New findings open a flag; a flag the user dismissed stays dismissed; an
    open flag whose problem has gone is resolved. Flags about the user's own
    Google events are left alone when their calendar could not be read.
    """
    result = ScanResult(found=len(findings))
    existing = {flag.dedupe_key: flag for flag in session.scalars(select(CalendarFlag))}
    current = set()
    for finding in findings:
        current.add(finding.key)
        flag = existing.get(finding.key)
        if flag is None:
            flag = CalendarFlag(
                kind=finding.kind, commitment_id=finding.commitment_id,
                other_commitment_id=finding.other_commitment_id, external_event_id=finding.external_id,
                details=finding.details, dedupe_key=finding.key, status="open",
            )
            session.add(flag)
            session.flush()
            _announce(session, flag, finding)
            result.new += 1
        elif flag.status == "resolved":
            flag.status, flag.resolved_at, flag.details = "open", None, finding.details
            _announce(session, flag, finding)
            result.new += 1
        elif flag.status == "open" and flag.details != finding.details:
            flag.details = finding.details
        result.flag_ids.append(flag.id)

    for key, flag in existing.items():
        if flag.status != "open" or key in current:
            continue
        if flag.external_event_id is not None and not external_checked:
            continue
        flag.status, flag.resolved_at = "resolved", now
        record_event(
            session, CALENDAR_FLAG_RESOLVED,
            f"No longer a {flag.kind}: {flag.details.get('items', [{}])[0].get('title', 'a commitment')}",
            entity_type="calendar_flag", entity_id=flag.id,
            payload={"kind": flag.kind, "how": "cleared"},
        )
        result.cleared += 1
    session.flush()
    return result


def scan(
    session: Session, external_events: list[dict], hours: WorkingHours, now: datetime, *, external_checked: bool,
) -> ScanResult:
    ours = [item for item in map(commitment_item, upcoming_commitments(session, now)) if item]
    external = [item for item in map(external_item, external_events) if item]
    return record(session, find_issues(ours, external, hours, now), now, external_checked=external_checked)
