"""The shared secret that guards the local API (Phase 10).

Why this exists at all: the calendar server listens on ``127.0.0.1``, and it is
tempting to treat that as security. It is not. Loopback is reachable by *any*
page the user has open — a random site can run ``fetch("http://127.0.0.1:8765/
api/commitments", …)`` in the background. Without a check, that page could read
what the tracker extracted from the user's email, or push events into their
calendar.

So every ``/api/*`` request must carry a token that only the browser extension
has, and which the user pastes in once. The token is generated on first use and
kept in the data directory, not the repo.

``/calendar.ics`` and ``/health`` stay open deliberately: a calendar app
subscribing to the feed cannot send a custom header, and the feed is the
product. That is a considered trade, not an oversight — see PROJECT_PLAN.md §16.
"""
from __future__ import annotations

import logging
import secrets
import stat

from src import config

log = logging.getLogger(__name__)

#: Header the extension sends. Not ``Authorization``: this is not a bearer token
#: in the OAuth sense, and using a distinct name keeps the two from being
#: confused in logs or forwarded by accident.
TOKEN_HEADER = "X-Tracker-Token"

#: Long enough that guessing is hopeless, short enough to paste by hand.
TOKEN_BYTES = 24


def token_path():
    return config.DATA_DIR / "api_token.txt"


def _harden(path) -> None:
    """Make the token file owner-readable where the OS supports it."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows ignores POSIX modes and NTFS ACLs already restrict the user's
        # own AppData. Failing to tighten permissions is not worth aborting for.
        log.debug("Could not tighten permissions on %s", path, exc_info=True)


def read_token() -> str | None:
    """The current token, or None if one has never been generated."""
    path = token_path()
    if not path.exists():
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        log.warning("Could not read the API token file.", exc_info=True)
        return None
    return value or None


def get_or_create_token() -> str:
    """The token, generating and persisting one the first time it is asked for."""
    existing = read_token()
    if existing:
        return existing

    token = secrets.token_urlsafe(TOKEN_BYTES)
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    _harden(path)
    log.info("Generated a new local API token at %s", path)
    return token


def rotate_token() -> str:
    """Replace the token, invalidating anything holding the old one.

    Offered because a token pasted into a browser extension is a credential the
    user may want to revoke — after sharing a screen, for instance.
    """
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    path.write_text(token, encoding="utf-8")
    _harden(path)
    log.info("Rotated the local API token.")
    return token


def token_matches(candidate: str | None) -> bool:
    """Whether a supplied token is the right one.

    Compared with :func:`secrets.compare_digest` so the check cannot be turned
    into an oracle by measuring how long a wrong answer takes to reject.
    """
    if not candidate:
        return False
    expected = read_token()
    if not expected:
        return False
    return secrets.compare_digest(candidate, expected)
