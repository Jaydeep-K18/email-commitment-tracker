"""First-run setup: what is missing, and how to record what the user supplies.

A packaged build has no ``.env`` to edit and no terminal to run
``scripts/set_imap_password.py`` in, so the dashboard has to be able to collect
the same two things itself: which mailbox to read, and the app password for it.

This lives beside :mod:`src.config` rather than in :mod:`desktop` because it is
about credentials, not packaging — the dashboard needs it whether or not it was
started from a tray icon.

The password only ever travels from the user's keystrokes into the OS keyring.
It is never written to ``.env``, never logged, and never returned by anything
here (PROJECT_PLAN.md §16).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import keyring

from src import config

log = logging.getLogger(__name__)

#: Key written to the user's .env. The password deliberately has no counterpart.
ENV_USER_KEY = "IMAP_USER"


@dataclass(frozen=True)
class SetupState:
    """What the app still needs before it can fetch anything."""

    has_user: bool
    has_password: bool

    @property
    def complete(self) -> bool:
        return self.has_user and self.has_password

    @property
    def missing(self) -> str:
        if not self.has_user:
            return "an email address"
        if not self.has_password:
            return "an app password"
        return ""


def password_is_stored(user: str | None = None) -> bool:
    """Whether the keyring already holds a password for this mailbox."""
    account = config.IMAP_USER if user is None else user
    if not account:
        return False
    try:
        return bool(keyring.get_password(config.KEYRING_SERVICE, account))
    except Exception:  # noqa: BLE001 - a locked or absent keyring is "no"
        log.debug("Keyring lookup failed.", exc_info=True)
        return False


def setup_state(user: str | None = None) -> SetupState:
    account = config.IMAP_USER if user is None else user
    return SetupState(
        has_user=bool(account),
        has_password=password_is_stored(account),
    )


def needs_setup() -> bool:
    """True when the dashboard should show setup instead of commitments."""
    return not setup_state().complete


def env_upsert(text: str, key: str, value: str) -> str:
    """Return ``text`` with ``key=value`` set, preserving everything else.

    Rewriting rather than appending matters because setup can be run more than
    once — a user correcting a typo in their address should not end up with two
    IMAP_USER lines, where the loser silently wins on the next parse.
    """
    lines = text.splitlines()
    replacement = f"{key}={value}"
    found = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.split("=", 1)[0].strip() == key:
            lines[index] = replacement
            found = True
    if not found:
        lines.append(replacement)
    return "\n".join(lines).strip() + "\n"


def save_email_address(address: str) -> None:
    """Persist the mailbox address to the user's own .env."""
    address = address.strip()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DATA_DIR / ".env"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(env_upsert(existing, ENV_USER_KEY, address), encoding="utf-8")
    # Keep the already-imported config in step, so the rest of this run uses the
    # new address without needing a restart.
    config.IMAP_USER = address


def save_password(address: str, password: str) -> None:
    """Store the app password in the OS keyring under this address."""
    keyring.set_password(config.KEYRING_SERVICE, address.strip(), password)


def check_connection(address: str, password: str) -> tuple[bool, str]:
    """Try an IMAP login and report the outcome in the user's language.

    Worth doing during setup: a wrong app password is by far the most common
    first-run failure, and without this the only symptom is a background cycle
    quietly failing later.
    """
    import imaplib

    try:
        with imaplib.IMAP4_SSL(config.IMAP_HOST, config.IMAP_PORT) as imap:
            imap.login(address.strip(), password)
            imap.select(config.IMAP_MAILBOX, readonly=True)
        return True, f"Connected to {config.IMAP_HOST} and opened {config.IMAP_MAILBOX}."
    except imaplib.IMAP4.error:
        # The server rejected the credentials rather than failing to be reached.
        return False, (
            "The server rejected those details. For Gmail this must be a "
            "16-character App Password, not your normal account password."
        )
    except OSError as exc:
        return False, f"Could not reach {config.IMAP_HOST}: {exc}"
