"""Data and aggregation for the dashboard, with no Streamlit dependency.

Streamlit reruns its script top-to-bottom on every interaction, which makes UI
modules an awkward place for logic. Everything the dashboard needs to *compute*
lives here instead, as plain functions over plain values, so it can be unit
tested without a browser or a running server.

The pages under ``dashboard/pages/`` are left as thin rendering on top of this.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src import config
from src.storage.database import calendar_candidates, list_commitments
from src.storage.models import Commitment
from src.sync.sync_engine import decide, review_queue

# --- Urgency --------------------------------------------------------------

OVERDUE = "overdue"
TODAY = "today"
URGENT = "urgent"
UPCOMING = "upcoming"
UNDATED = "undated"

#: Colour and label per urgency band, used for the feed's colour coding.
URGENCY_STYLE: dict[str, tuple[str, str]] = {
    OVERDUE: ("#d92b2b", "Overdue"),
    TODAY: ("#e8710a", "Today"),
    URGENT: ("#e0a800", "Soon"),
    UPCOMING: ("#2e7d32", "Upcoming"),
    UNDATED: ("#6b7280", "No date"),
}

#: Emoji marker per band — Streamlit renders these inline in labels.
URGENCY_ICON: dict[str, str] = {
    OVERDUE: "🔴",
    TODAY: "🟠",
    URGENT: "🟡",
    UPCOMING: "🟢",
    UNDATED: "⚪",
}


def urgency(deadline: datetime | None, now: datetime | None = None) -> str:
    """Classify how pressing a deadline is.

    Comparison is by calendar day, not elapsed hours: a deadline at 09:00 today
    is still "today" at 17:00, because that is how a person reads their own
    calendar, even though the moment has passed.
    """
    if deadline is None:
        return UNDATED

    now = now or datetime.now()
    deadline_day: date = deadline.date()
    today: date = now.date()

    if deadline_day < today:
        return OVERDUE
    if deadline_day == today:
        return TODAY
    if deadline_day <= today + timedelta(days=config.DASHBOARD_URGENT_DAYS):
        return URGENT
    return UPCOMING


def urgency_icon(deadline: datetime | None, now: datetime | None = None) -> str:
    return URGENCY_ICON[urgency(deadline, now=now)]


def format_deadline(deadline: datetime | None) -> str:
    """Human-readable deadline; date-only when the time is midnight."""
    if deadline is None:
        return "No deadline"
    if deadline.time() == datetime.min.time():
        return deadline.strftime("%a %d %b %Y")
    return deadline.strftime("%a %d %b %Y, %H:%M")


def relative_deadline(deadline: datetime | None, now: datetime | None = None) -> str:
    """"in 3 days" / "today" / "2 days overdue", by whole calendar days."""
    if deadline is None:
        return ""
    now = now or datetime.now()
    days = (deadline.date() - now.date()).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days == -1:
        return "1 day overdue"
    if days < 0:
        return f"{abs(days)} days overdue"
    return f"in {days} days"


# --- Type labels ----------------------------------------------------------

TYPE_LABEL = {
    "deadline_on_you": "You owe",
    "deadline_from_others": "Owed to you",
    "meeting": "Meeting",
    "question_pending": "Question",
}

TYPE_ICON = {
    "deadline_on_you": "📤",
    "deadline_from_others": "📥",
    "meeting": "📅",
    "question_pending": "❓",
}


def type_label(commitment_type: str) -> str:
    return TYPE_LABEL.get(commitment_type, commitment_type)


# --- Snapshot -------------------------------------------------------------

@dataclass
class Snapshot:
    """Today's numbers for the overview page."""

    total_open: int = 0
    overdue: int = 0
    due_today: int = 0
    due_this_week: int = 0
    on_calendar: int = 0
    awaiting_approval: int = 0
    questions_pending: int = 0
    you_owe: int = 0
    owed_to_you: int = 0


def build_snapshot(session: Session, now: datetime | None = None) -> Snapshot:
    """Aggregate the current state of play for the overview page."""
    now = now or datetime.now()
    snapshot = Snapshot()

    open_rows = list(
        session.execute(
            select(Commitment).where(
                Commitment.status.not_in(("dismissed", "superseded", "fulfilled"))
            )
        )
        .scalars()
        .all()
    )

    snapshot.total_open = len(open_rows)
    week_end = now.date() + timedelta(days=7)

    for commitment in open_rows:
        band = urgency(commitment.deadline, now=now)
        if band == OVERDUE:
            snapshot.overdue += 1
        elif band == TODAY:
            snapshot.due_today += 1

        if commitment.deadline and now.date() <= commitment.deadline.date() <= week_end:
            snapshot.due_this_week += 1

        if commitment.type == "question_pending":
            snapshot.questions_pending += 1
        elif commitment.type == "deadline_on_you":
            snapshot.you_owe += 1
        elif commitment.type == "deadline_from_others":
            snapshot.owed_to_you += 1

    snapshot.on_calendar = sum(
        1 for c in calendar_candidates(session) if decide(c).should_sync
    )
    snapshot.awaiting_approval = len(review_queue(session))
    return snapshot


# --- Feed -----------------------------------------------------------------

def filter_commitments(
    commitments: list[Commitment],
    *,
    types: list[str] | None = None,
    tiers: list[str] | None = None,
    statuses: list[str] | None = None,
    urgencies: list[str] | None = None,
    search: str | None = None,
    now: datetime | None = None,
) -> list[Commitment]:
    """Apply the feed's filters. An empty or absent filter means "everything"."""
    results = list(commitments)

    if types:
        results = [c for c in results if c.type in types]
    if tiers:
        results = [c for c in results if (c.vip_tier or "untiered") in tiers]
    if statuses:
        results = [c for c in results if c.status in statuses]
    if urgencies:
        results = [c for c in results if urgency(c.deadline, now=now) in urgencies]
    if search:
        needle = search.strip().lower()
        if needle:
            results = [c for c in results if _matches(c, needle)]
    return results


def _matches(commitment: Commitment, needle: str) -> bool:
    haystack = " ".join(
        part
        for part in (
            commitment.subject,
            commitment.counterparty_name,
            commitment.counterparty_email,
            commitment.evidence_quote,
        )
        if part
    )
    return needle in haystack.lower()


def sort_by_urgency(
    commitments: list[Commitment], now: datetime | None = None
) -> list[Commitment]:
    """Soonest deadline first; undated commitments last."""
    return sorted(
        commitments,
        key=lambda c: (c.deadline is None, c.deadline or datetime.max),
    )


def feed_commitments(
    session: Session, include_closed: bool = False, **filters
) -> list[Commitment]:
    """The full commitment list for the feed page, filtered and ordered."""
    rows = list_commitments(session)
    if not include_closed:
        rows = [c for c in rows if c.status not in ("dismissed", "superseded")]
    return sort_by_urgency(filter_commitments(rows, **filters))


def pending_questions(session: Session) -> list[Commitment]:
    """Open questions awaiting a reply.

    These never reach the calendar by design, so the dashboard is the only place
    they surface — which is why the review page lists them alongside the
    approval queue.
    """
    rows = list_commitments(session, commitment_type="question_pending")
    return [c for c in rows if c.status not in ("dismissed", "superseded", "fulfilled")]
