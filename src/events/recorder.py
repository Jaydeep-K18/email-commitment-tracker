"""Record what happened, in the same transaction as the change itself.

Every caller passes the session it is already using for the state change. That
is the whole point of the design: the event row and the change it describes
commit together or not at all, so the activity timeline can never claim
something happened that the database does not show, and the outbox relay can
never publish an event for a change that was rolled back.

Nothing here talks to Kafka. Publishing is the relay's job, after commit
(:mod:`src.events.relay`).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from src.storage.models import Event

log = logging.getLogger(__name__)

# --- Event types ----------------------------------------------------------
# The catalogue is mirrored in packages/shared (TypeScript) for the server and
# the React app; a contract test keeps the two lists identical.

EMAIL_RECEIVED = "email.received"
EMAIL_CLASSIFIED = "email.classified"
EMAIL_ANALYZED = "email.analyzed"
EXTRACTION_FAILED = "extraction.failed"
COMMITMENT_CREATED = "commitment.created"
COMMITMENT_SUPERSEDED = "commitment.superseded"
CALENDAR_EVENT_CREATED = "calendar.event_created"
CALENDAR_EVENT_UPDATED = "calendar.event_updated"
CALENDAR_EVENT_FAILED = "calendar.event_failed"
CALENDAR_EVENT_REMOVED = "calendar.event_removed"
CALENDAR_SYNCED = "calendar.synced"
JOB_QUEUED = "job.queued"
JOB_STARTED = "job.started"
JOB_COMPLETED = "job.completed"
JOB_FAILED = "job.failed"
JOB_RETRYING = "job.retrying"
JOB_RETRIED = "job.retried"
SYSTEM_WORKER_STARTED = "system.worker_started"
SYSTEM_WORKER_STOPPED = "system.worker_stopped"
SYSTEM_ERROR = "system.error"

# Recorded by the Node server: what the user did. Part of the same audit trail,
# so "who archived this, and when" is answered by the same timeline as "when
# did it arrive".
EMAIL_UPDATED = "email.updated"
EMAIL_BULK_UPDATED = "email.bulk_updated"
COMMITMENT_UPDATED = "commitment.updated"
CONTACT_CREATED = "contact.created"
CONTACT_UPDATED = "contact.updated"
CONTACT_DELETED = "contact.deleted"
SETTINGS_UPDATED = "settings.updated"
AUTH_SETUP = "auth.setup"
AUTH_LOGIN = "auth.login"
AUTH_LOGIN_FAILED = "auth.login_failed"
AUTH_LOGOUT = "auth.logout"
AUTH_PASSWORD_CHANGED = "auth.password_changed"
DATA_EXPORTED = "data.exported"
DATA_PURGED = "data.purged"

EVENT_TYPES: tuple[str, ...] = (
    EMAIL_RECEIVED,
    EMAIL_CLASSIFIED,
    EMAIL_ANALYZED,
    EXTRACTION_FAILED,
    COMMITMENT_CREATED,
    COMMITMENT_SUPERSEDED,
    CALENDAR_EVENT_CREATED,
    CALENDAR_EVENT_UPDATED,
    CALENDAR_EVENT_FAILED,
    CALENDAR_EVENT_REMOVED,
    CALENDAR_SYNCED,
    JOB_QUEUED,
    JOB_STARTED,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_RETRYING,
    JOB_RETRIED,
    SYSTEM_WORKER_STARTED,
    SYSTEM_WORKER_STOPPED,
    SYSTEM_ERROR,
    EMAIL_UPDATED,
    EMAIL_BULK_UPDATED,
    COMMITMENT_UPDATED,
    CONTACT_CREATED,
    CONTACT_UPDATED,
    CONTACT_DELETED,
    SETTINGS_UPDATED,
    AUTH_SETUP,
    AUTH_LOGIN,
    AUTH_LOGIN_FAILED,
    AUTH_LOGOUT,
    AUTH_PASSWORD_CHANGED,
    DATA_EXPORTED,
    DATA_PURGED,
)

SEVERITIES = ("info", "success", "warning", "error")


def email_correlation(email_id: int | None) -> str | None:
    """The id that ties every step of one email's journey together."""
    return f"email:{email_id}" if email_id is not None else None


def record_event(
    session: Session,
    type: str,  # noqa: A002 - mirrors the column name
    message: str,
    *,
    entity_type: str | None = None,
    entity_id: Any = None,
    correlation_id: str | None = None,
    severity: str = "info",
    payload: dict | None = None,
    source: str = "worker",
) -> Event:
    """Add an event to ``session``. It commits when the caller's change does.

    An unknown type or severity raises immediately rather than being stored:
    the consumers dispatch on these strings, and an event no consumer
    recognises is an event nobody sees.
    """
    if type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {type!r}")
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity {severity!r}")

    event = Event(
        type=type,
        message=message,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        correlation_id=correlation_id,
        severity=severity,
        payload=payload or {},
        source=source,
    )
    session.add(event)
    return event
