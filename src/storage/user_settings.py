"""Preferences the user saved in the app, as the worker sees them.

The Node server validates and writes these (one JSON row per section, shape
defined by the shared zod schemas); the worker only reads them. A missing row or
key falls back to the environment, so a worker started before anyone opened the
settings page behaves exactly as it did before there was a settings page.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from src import config
from src.storage.models import Setting


def section(session: Session, name: str) -> dict[str, Any]:
    row = session.get(Setting, name)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


def get(session: Session, name: str, key: str, default: Any) -> Any:
    return section(session, name).get(key, default)


def calendar_target(session: Session) -> str:
    """auto / ics / google. The app's setting wins over the legacy env var."""
    saved = get(session, "calendar", "target", None)
    if saved in ("auto", "ics", "google"):
        return saved
    legacy = (config.CALENDAR_TARGET or "").strip().lower()
    return legacy if legacy in ("ics", "google") else "auto"


def fetch_interval_minutes(session: Session) -> int:
    value = get(session, "email", "fetchIntervalMinutes", config.FETCH_INTERVAL_MINUTES)
    return max(5, int(value))


def apply_fetch_settings(session: Session) -> None:
    """Point the fetcher at the window the user chose, before a fetch runs.

    The fetcher reads these module-level values; only the worker fetches, so
    setting them here, at the start of each fetch job, is what makes a change
    on the settings page take effect on the next mailbox check.
    """
    email = section(session, "email")
    if "lookbackDays" in email:
        config.FETCH_LOOKBACK_DAYS = int(email["lookbackDays"])
    if "maxPerFetch" in email:
        config.FETCH_MAX_EMAILS = int(email["maxPerFetch"])
