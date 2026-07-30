"""Central configuration for the Email Commitment Tracker.

Non-secret settings are loaded from the environment (via a gitignored ``.env``
file). The IMAP password is the one secret, and it is read from the OS keyring
rather than any file — see PROJECT_PLAN.md §16 (Security and Privacy).
"""
from __future__ import annotations

import os
from pathlib import Path

import keyring
from dotenv import load_dotenv

# Project root is the parent of this ``src`` package.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load non-secret config from .env at import time (no-op if the file is absent).
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
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "tracker.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

# --- IMAP connection (non-secret; the password lives in the keyring) ---
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = _get_int("IMAP_PORT", 993)
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_MAILBOX = os.getenv("IMAP_MAILBOX", "INBOX")
IMAP_USE_SSL = _get_bool("IMAP_USE_SSL", True)

# --- Fetch window ---
FETCH_LOOKBACK_DAYS = _get_int("FETCH_LOOKBACK_DAYS", 7)
FETCH_MAX_EMAILS = _get_int("FETCH_MAX_EMAILS", 50)

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
