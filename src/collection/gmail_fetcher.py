"""Fetch mail through the Gmail API instead of IMAP (Phase 9).

Exists so a user can sign in with Google once rather than enabling 2-Step
Verification and generating a 16-character app password by hand — the step where
most people trying the app give up.

It deliberately returns the *same* thing the IMAP fetcher returns: a list of raw
RFC822 byte strings. Everything downstream — the parser, the VIP filter, dedup,
storage — is then shared, so the two transports cannot drift into extracting
different things from the same message.

Reading is non-destructive: `format="raw"` with no modification of labels, so
the user's unread state is untouched, matching the IMAP path's ``BODY.PEEK[]``.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone

from src import config
from src.auth import google_auth

log = logging.getLogger(__name__)


def build_service(credentials=None):
    """A Gmail API client. Separated so tests can inject a fake."""
    from googleapiclient.discovery import build

    creds = credentials or google_auth.credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def search_query(lookback_days: int | None = None) -> str:
    """The Gmail search that mirrors the IMAP ``SINCE`` window.

    ``after:`` takes a date, so this is the same day-granularity window the IMAP
    path uses rather than a stricter one.
    """
    if config.GOOGLE_GMAIL_QUERY.strip():
        return config.GOOGLE_GMAIL_QUERY.strip()

    days = config.FETCH_LOOKBACK_DAYS if lookback_days is None else lookback_days
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return f"after:{since.strftime('%Y/%m/%d')}"


def fetch_recent(service=None) -> list[bytes]:
    """Raw RFC822 bytes for recent messages, oldest first.

    Ordered oldest-first to match the IMAP path, so a capped batch keeps the
    same messages either way and the two transports stay interchangeable.
    """
    service = service or build_service()
    query = search_query()

    listing = (
        service.users()
        .messages()
        .list(
            userId="me",
            q=query,
            maxResults=max(config.FETCH_MAX_EMAILS, 1),
        )
        .execute()
    )
    messages = listing.get("messages", [])
    if not messages:
        return []

    # Gmail lists newest first; reverse so the batch reads oldest to newest.
    messages = list(reversed(messages))

    raws: list[bytes] = []
    for message in messages:
        try:
            detail = (
                service.users()
                .messages()
                .get(userId="me", id=message["id"], format="raw")
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 - one bad message, not the batch
            log.warning("Could not fetch Gmail message %s: %s", message.get("id"), exc)
            continue

        raw = detail.get("raw")
        if not raw:
            continue
        # Gmail returns URL-safe base64, which differs from standard base64 in
        # two characters; decoding with the wrong alphabet corrupts the message.
        raws.append(base64.urlsafe_b64decode(raw))

    log.info("Fetched %d message(s) from the Gmail API (%s).", len(raws), query)
    return raws


def is_available() -> bool:
    """Whether the Gmail API can be used, without making a request."""
    return google_auth.is_signed_in()
