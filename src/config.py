"""Central configuration for the Email Commitment Tracker.

Non-secret settings are loaded from the environment (via a gitignored ``.env``
file). The IMAP password is the one secret, and it is read from the OS keyring
rather than any file — see PROJECT_PLAN.md §16 (Security and Privacy).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

import keyring
from dotenv import load_dotenv

#: Name used for the per-user data directory of a packaged build.
APP_NAME = "EmailCommitmentTracker"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle rather than a checkout."""
    return bool(getattr(sys, "frozen", False))


def resource_dir(*, frozen: bool | None = None) -> Path:
    """Where the app's own read-only files live.

    PyInstaller unpacks a one-file build into a temporary directory and sets
    ``sys._MEIPASS`` to it, so bundled resources are found there rather than
    next to ``__file__``.
    """
    if frozen if frozen is not None else is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return Path(__file__).resolve().parent.parent


def user_data_dir(
    *,
    frozen: bool | None = None,
    base_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    """Where the database, the .ics file and .env live.

    In a checkout this stays ``<repo>/data`` so development is unchanged. A
    packaged build must not write there: ``_MEIPASS`` is a temp directory that
    is deleted when the app exits, so the database would be lost on every quit,
    and an installed .exe may sit somewhere the user cannot write at all.
    """
    env = os.environ if environ is None else environ
    override = env.get("ECT_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    frozen = is_frozen() if frozen is None else frozen
    if not frozen:
        return (base_dir or resource_dir(frozen=False)) / "data"

    system = platform or sys.platform
    if system == "win32":
        root = env.get("LOCALAPPDATA") or env.get("APPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    elif system == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg = env.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP_NAME


# Read-only app files (bundled resources when frozen, the repo in a checkout).
BASE_DIR = resource_dir()
# Writable per-user state. Kept separate from BASE_DIR so a packaged build
# stores the database outside the bundle.
DATA_DIR = user_data_dir(base_dir=BASE_DIR)

# Load non-secret config from .env at import time (no-op if the file is absent).
# A packaged build has no repo to read, so the user's own data directory is
# checked first and the bundled copy is the fallback.
load_dotenv(DATA_DIR / ".env")
load_dotenv(BASE_DIR / ".env")


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- Paths ---
# DATA_DIR is resolved above, before .env is loaded from it.
DB_PATH = DATA_DIR / "tracker.db"
SQLITE_URL = f"sqlite:///{DB_PATH}"


def normalize_database_url(url: str) -> str:
    """Translate a Postgres URL into the form SQLAlchemy needs.

    The Node server and the Python worker share one ``DATABASE_URL``. Node's
    driver wants the conventional ``postgres://`` scheme, while SQLAlchemy needs
    to be told which driver to load, so the scheme is rewritten here rather than
    asking the user to keep two copies of the same connection string in step.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


# Postgres is the database of the full-stack app. The SQLite file remains the
# default so the unit tests and a checkout without Docker still run, and it is
# the source the one-off migration into Postgres reads from.
DATABASE_URL = normalize_database_url(os.getenv("DATABASE_URL", SQLITE_URL))
ICS_PATH = DATA_DIR / "calendar.ics"

# --- IMAP connection (non-secret; the password lives in the keyring) ---
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = _get_int("IMAP_PORT", 993)
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_MAILBOX = os.getenv("IMAP_MAILBOX", "INBOX")
IMAP_USE_SSL = _get_bool("IMAP_USE_SSL", True)

# --- Fetch window ---
FETCH_LOOKBACK_DAYS = _get_int("FETCH_LOOKBACK_DAYS", 7)
FETCH_MAX_EMAILS = _get_int("FETCH_MAX_EMAILS", 50)

# --- Google (Phase 9) ---
# The OAuth client the user creates in their own Google Cloud project. It is not
# a secret in the usual sense — Google classes installed-app clients as public —
# but it is per-user, so it lives in the data directory rather than the repo.
# A packaged build also carries an embedded client (see google_client.py) so a
# user never has to create a Cloud project; this file, when present, wins.
GOOGLE_CLIENT_SECRETS = DATA_DIR / os.getenv(
    "GOOGLE_CLIENT_SECRETS_NAME", "google_client_secret.json"
)

# Scopes are requested in three separate grants rather than one, because Google
# prices them very differently and bundling them would drag the whole app into
# the most expensive tier:
#
#   identity  (openid, userinfo.email)  basic      — no verification, no user cap
#   calendar  (calendar.events)         sensitive  — verification review, free
#   mail      (gmail.readonly)          restricted — verification PLUS an annual
#                                                    third-party security audit
#
# So sign-in asks only for identity, and a user who exports .ics for Outlook
# never grants a sensitive scope at all. Calendar is requested later by
# incremental authorization, and only if the user picks Google Calendar.
GOOGLE_IDENTITY_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
)
# Events-only. Deliberately not `calendar`, which would also grant the power to
# delete whole calendars.
GOOGLE_CALENDAR_SCOPES = GOOGLE_IDENTITY_SCOPES + (
    "https://www.googleapis.com/auth/calendar.events",
)
# Only reachable for users who supply their own Cloud project — see above.
GOOGLE_MAIL_SCOPES = GOOGLE_CALENDAR_SCOPES + (
    "https://www.googleapis.com/auth/gmail.readonly",
)
# Which calendar events are written to. "primary" is the user's default.
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
# Loopback port for the consent redirect. 0 lets the OS choose a free one,
# which avoids a collision with the dashboard or the calendar server.
GOOGLE_OAUTH_PORT = _get_int("GOOGLE_OAUTH_PORT", 0)
# Gmail search used when fetching through the API, mirroring the IMAP window.
GOOGLE_GMAIL_QUERY = os.getenv("GOOGLE_GMAIL_QUERY", "")

# --- Local LLM (Ollama) ---
# All inference is local; nothing here reaches an external service.
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest")
# CPU inference on a 3B model can take tens of seconds per email.
OLLAMA_TIMEOUT = _get_int("OLLAMA_TIMEOUT", 180)
# Emails longer than this are truncated before prompting (keeps latency sane).
EXTRACTION_MAX_BODY_CHARS = _get_int("EXTRACTION_MAX_BODY_CHARS", 6000)
# Records below this confidence are discarded. When the model reports very low
# confidence it is usually flagging its own guess — take it at its word.
EXTRACTION_MIN_CONFIDENCE = _get_float("EXTRACTION_MIN_CONFIDENCE", 0.3)

# --- Calendar output ---
# Where events go. The .ics file is written either way — it costs nothing and is
# the only output that works with no account and no network. This governs
# whether Google Calendar is *also* pushed to, so the app is never locked to one
# calendar vendor.
CALENDAR_TARGET_ICS = "ics"
CALENDAR_TARGET_GOOGLE = "google"
# Empty means "decide from what the user granted": having consented to calendar
# access is itself the signal that they want it used. Setting this to "ics"
# explicitly opts out of Google even while signed in, which is what the setup
# screen writes when the user picks a different calendar app.
CALENDAR_TARGET = os.getenv("CALENDAR_TARGET", "")

CALENDAR_NAME = os.getenv("CALENDAR_NAME", "Email Commitments")
# Reminder lead time for events that have a specific time of day.
CALENDAR_REMINDER_MINUTES = _get_int("CALENDAR_REMINDER_MINUTES", 30)
# Reminder lead time for all-day (date-only) deadlines.
CALENDAR_ALLDAY_REMINDER_HOURS = _get_int("CALENDAR_ALLDAY_REMINDER_HOURS", 24)
# How often subscribed calendar apps are asked to re-poll the feed.
CALENDAR_REFRESH_MINUTES = _get_int("CALENDAR_REFRESH_MINUTES", 60)

# --- Sync engine (Phase 5) ---
# How many consecutive failed publishes to keep retrying before giving up on a
# commitment. Failures are recorded in sync_log either way.
SYNC_MAX_RETRIES = _get_int("SYNC_MAX_RETRIES", 3)
# How similar two commitment subjects must be (0-1 word overlap) before a
# follow-up email is treated as updating an existing commitment rather than
# creating a new one. Set high enough that unrelated deadlines from the same
# person are not merged together.
SYNC_DUPLICATE_THRESHOLD = _get_float("SYNC_DUPLICATE_THRESHOLD", 0.6)

# --- Local calendar server ---
# Binds to loopback by default: the feed is not exposed to the network at all.
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = _get_int("SERVER_PORT", 8765)

# --- Background scheduler (Phase 6) ---
# How often the automatic fetch -> extract -> sync cycle runs.
SCHEDULER_INTERVAL_MINUTES = _get_int("SCHEDULER_INTERVAL_MINUTES", 30)
# Extraction is the slow step (local LLM inference on CPU). Turning this off
# leaves the scheduler fetching and syncing only.
SCHEDULER_RUN_EXTRACTION = _get_bool("SCHEDULER_RUN_EXTRACTION", True)
# Emails per automatic cycle. Kept modest so a scheduled run cannot occupy the
# machine for a very long time.
SCHEDULER_EXTRACTION_LIMIT = _get_int("SCHEDULER_EXTRACTION_LIMIT", 10)

# --- Dashboard (Phase 6) ---
# A deadline this many days out or nearer is shown as urgent.
DASHBOARD_URGENT_DAYS = _get_int("DASHBOARD_URGENT_DAYS", 3)

# --- Keyring ---
KEYRING_SERVICE = "email_commitment_tracker"


class ConfigError(RuntimeError):
    """Raised when required configuration or credentials are missing."""


def get_imap_password() -> str:
    """Return the IMAP password from the OS keyring.

    The password is never written to ``.env`` or any file. Store it once with::

        python -m scripts.set_imap_password
    """
    if not IMAP_USER:
        raise ConfigError(
            "IMAP_USER is not set. Copy .env.example to .env and set IMAP_USER "
            "to your email address."
        )
    password = keyring.get_password(KEYRING_SERVICE, IMAP_USER)
    if not password:
        raise ConfigError(
            f"No IMAP password found in the keyring for '{IMAP_USER}'. "
            "Store it once with:  python -m scripts.set_imap_password"
        )
    return password
