"""Write commitments into the user's Google Calendar.

The ``.ics`` feed this module sits beside is served from ``127.0.0.1``, and
Google fetches subscription URLs from its own servers — which cannot reach a
loopback address on the user's laptop. A Google Calendar subscription to the
local feed therefore never works, no matter how correct the feed is. This module
is the route that actually puts events on the user's phone.

Only the extracted commitment travels: a title, a date, the evidence sentence,
and the contact. No email body, and nothing goes to any third party other than
the user's own Google account — which already holds the mail it came from.

Rendering deliberately mirrors :mod:`src.sync.ics_builder` (same titles, same
description, same reminder lead times) so the two calendars cannot disagree
about what a commitment says. Selection is not decided here at all: it comes
from :func:`src.sync.sync_engine.calendar_commitments`, which owns tier policy.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src import config
from src.auth import google_auth
from src.storage.models import Commitment
from src.sync.ics_builder import build_description, build_summary, is_all_day

log = logging.getLogger(__name__)

#: Marks events this app owns, so a sweep can tell them apart from events the
#: user created by hand in the same calendar. Google returns these on read.
APP_TAG_KEY = "emailCommitmentTracker"
APP_TAG_VALUE = "1"


class GoogleCalendarError(RuntimeError):
    """Raised when the calendar cannot be reached or refuses a write."""


@dataclass
class PushReport:
    """What one push to Google achieved."""

    created: int = 0
    updated: int = 0
    removed: int = 0
    failed: list[tuple[int, str]] = field(default_factory=list)
    #: Which commitments got a brand-new event, so each can be recorded on its
    #: email's timeline. Counts alone cannot say which email it was.
    created_ids: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        line = (
            f"{self.created} created, {self.updated} updated, "
            f"{self.removed} removed"
        )
        if self.failed:
            line += f"  [{len(self.failed)} failed]"
        return line


def build_service(credentials=None):
    """A Calendar API client. Separated so tests can inject a fake."""
    from googleapiclient.discovery import build

    creds = credentials or google_auth.credentials()
    # cache_discovery=False: the default file cache warns noisily and is
    # useless in a packaged build with a read-only bundle directory.
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _local_rfc3339(when: datetime) -> str:
    """A naive wall-clock time as RFC 3339 with this machine's UTC offset.

    Google rejects a naive ``dateTime`` outright — "Missing time zone definition
    for start time" — where iCalendar happily accepts one as floating local time.
    That difference is why the ``.ics`` feed worked while every *timed* event was
    refused by the API.

    Attaching the offset (rather than a ``timeZone`` field) needs no IANA
    database and no extra dependency, and ``astimezone()`` on a naive value
    resolves the offset *for that date*, so a deadline on the far side of a DST
    change still lands on the wall-clock time the email actually said.
    """
    return when.astimezone().isoformat()


def event_body(commitment: Commitment) -> dict:
    """Map a commitment onto a Google Calendar event resource.

    Deadlines are stored as the wall-clock time written in the email, so "5pm"
    must stay 5pm rather than being reinterpreted as UTC and shown at 22:30.
    Timed events carry this machine's UTC offset to preserve that; all-day
    events use a bare ``date``, which is timezone-less by definition.
    """
    if commitment.deadline is None:
        raise ValueError("commitment has no deadline and cannot become an event")

    body: dict = {
        "summary": build_summary(commitment),
        "description": build_description(commitment),
        # Survives round-tripping, and is how a cleanup pass recognises its own
        # events without keeping a second index.
        "extendedProperties": {"private": {APP_TAG_KEY: APP_TAG_VALUE}},
    }

    if is_all_day(commitment.deadline):
        start = commitment.deadline.date()
        # Google treats an all-day "end" as exclusive, same as RFC 5545.
        body["start"] = {"date": start.isoformat()}
        body["end"] = {"date": (start + timedelta(days=1)).isoformat()}
        minutes = config.CALENDAR_ALLDAY_REMINDER_HOURS * 60
        body["transparency"] = "transparent"
    else:
        body["start"] = {"dateTime": _local_rfc3339(commitment.deadline)}
        body["end"] = {
            "dateTime": _local_rfc3339(commitment.deadline + timedelta(hours=1))
        }
        minutes = config.CALENDAR_REMINDER_MINUTES

    body["reminders"] = {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": minutes}],
    }
    return body


def deterministic_event_id(install: str, commitment_id: int) -> str:
    """The Google event id this app will use for a commitment, chosen up front.

    Google lets a client choose an event's id on insert (base32hex: a-v and
    0-9, 5-1024 characters), and refuses a second insert with the same id with
    409 Conflict. Choosing the id ourselves is what makes creating an event
    idempotent: if an insert reached Google but its response was lost — a
    timeout, a crash, a worker killed mid-request — the retry cannot create a
    second copy. It hits the 409 instead, and becomes an update.

    Salted with the installation id so two databases writing to one calendar
    cannot claim each other's events.
    """
    digest = hashlib.sha256(f"{install}:{commitment_id}".encode()).digest()
    return "ect" + base64.b32hexencode(digest).decode().lower().rstrip("=")[:26]


def event_content_hash(commitment: Commitment) -> str:
    """A fingerprint of exactly what would be sent to Google for this commitment."""
    body = json.dumps(event_body(commitment), sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:32]


def is_retryable(exc: BaseException) -> bool:
    """Whether trying the same request again could succeed.

    Rate limits and server errors pass; a malformed event or a revoked sign-in
    will fail identically however many times it is retried, so retrying only
    delays telling the user.
    """
    from googleapiclient.errors import HttpError

    if google_auth.needs_sign_in(exc):
        return False
    if isinstance(exc, HttpError):
        status = exc.resp.status
        if status == 403:
            # 403 means both "rate limited" and "forbidden"; only the first
            # is worth waiting for.
            return "rateLimitExceeded" in str(exc) or "userRateLimitExceeded" in str(exc)
        return status == 429 or status >= 500
    return True   # network errors, timeouts


def _push_one(service, session, commitment: Commitment, install: str | None = None) -> str:
    """Create or update one event. Returns "created" or "updated"."""
    from googleapiclient.errors import HttpError

    body = event_body(commitment)
    calendar_id = config.GOOGLE_CALENDAR_ID

    if commitment.gcal_event_id:
        try:
            service.events().patch(
                calendarId=calendar_id,
                eventId=commitment.gcal_event_id,
                body=body,
            ).execute()
            return "updated"
        except HttpError as exc:
            if exc.resp.status not in (404, 410):
                raise
            # The user deleted the event by hand, or it was purged. Forget the
            # stale id and fall through to creating a fresh one, rather than
            # failing this commitment forever.
            log.info(
                "Event %s for commitment %s is gone; recreating.",
                commitment.gcal_event_id, commitment.id,
            )
            commitment.gcal_event_id = None

    if install is None:
        from src.storage.system_settings import install_id

        install = install_id(session)
    event_id = deterministic_event_id(install, commitment.id)

    try:
        created = service.events().insert(
            calendarId=calendar_id, body={**body, "id": event_id}
        ).execute()
        outcome = "created"
        commitment.gcal_event_id = created.get("id", event_id)
    except HttpError as exc:
        if exc.resp.status != 409:
            raise
        # The event already exists: an earlier attempt got through but its
        # response never arrived, or the user deleted it (Google keeps a
        # deleted event's id). Update it in place, restoring it if cancelled.
        log.info("Event %s already exists; updating instead of creating.", event_id)
        service.events().patch(
            calendarId=calendar_id, eventId=event_id, body={**body, "status": "confirmed"}
        ).execute()
        outcome = "updated"
        commitment.gcal_event_id = event_id

    session.flush()
    return outcome


def push_commitment(session, commitment: Commitment, service=None) -> str:
    """Push one commitment and record what Google accepted. Raises on failure.

    The unit of work for a background push job, which owns retrying.
    """
    service = service or build_service()
    outcome = _push_one(service, session, commitment)
    commitment.gcal_synced_hash = event_content_hash(commitment)
    session.flush()
    return outcome


def push(session, commitments: list[Commitment], service=None) -> PushReport:
    """Publish the given commitments to Google Calendar.

    Failures are collected per commitment rather than raised, so one rejected
    event cannot stop the rest of the batch from syncing.
    """
    report = PushReport()
    if not commitments:
        return report

    service = service or build_service()

    for commitment in commitments:
        try:
            outcome = push_commitment(session, commitment, service=service)
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            log.warning(
                "Could not sync commitment %s to Google: %s", commitment.id, exc
            )
            report.failed.append((commitment.id, str(exc)))
            continue
        if outcome == "created":
            report.created += 1
            report.created_ids.append(commitment.id)
        else:
            report.updated += 1

    return report


def remove(session, commitments: list[Commitment], service=None) -> int:
    """Delete events for commitments that should no longer be on the calendar.

    Used when a commitment is dismissed or superseded: without this the event
    would linger in Google Calendar even though the app no longer lists it.
    """
    from googleapiclient.errors import HttpError

    targets = [c for c in commitments if c.gcal_event_id]
    if not targets:
        return 0

    service = service or build_service()
    removed = 0

    for commitment in targets:
        try:
            service.events().delete(
                calendarId=config.GOOGLE_CALENDAR_ID,
                eventId=commitment.gcal_event_id,
            ).execute()
        except HttpError as exc:
            if exc.resp.status not in (404, 410):
                log.warning(
                    "Could not delete event for commitment %s: %s",
                    commitment.id, exc,
                )
                continue
            # Already gone is the outcome we wanted.
        commitment.gcal_event_id = None
        removed += 1

    session.flush()
    return removed


def is_available() -> bool:
    """Whether a Google push can even be attempted, without doing one.

    Signing in is no longer enough. Identity-only is the default consent, so a
    signed-in user may never have granted calendar access at all — and a user who
    chose ``.ics`` output deliberately has not. Checking the granted scope here
    keeps every sync cycle from attempting a push that can only 403, and keeps
    the report's ``google_connected`` honest.
    """
    account = google_auth.account()
    if account is None or not account.has_calendar:
        return False
    # Granting calendar access is the opt-in; this only lets a user who chose a
    # different calendar app turn the push off without revoking the scope.
    return _calendar_target() != config.CALENDAR_TARGET_ICS


def _calendar_target() -> str:
    """The user's choice from the settings page, else the legacy env var."""
    try:
        from src.storage.database import session_scope
        from src.storage.user_settings import calendar_target

        with session_scope() as session:
            return calendar_target(session)
    except Exception:  # noqa: BLE001 - the settings table is optional here
        log.debug("Calendar target setting unavailable; using the environment.", exc_info=True)
        return config.CALENDAR_TARGET
