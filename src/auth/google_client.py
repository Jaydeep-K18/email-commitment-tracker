"""The OAuth client the packaged app ships with.

A user downloading a built app cannot be asked to create a Google Cloud project,
so the binary has to carry a client of its own.

**This is not a leaked secret.** Google classifies installed-app clients as
*public*: the "client secret" cannot be kept confidential inside a binary the
user has on disk, and Google's own documentation says it is not treated as one.
RFC 8252 (OAuth 2.0 for Native Apps) says the same, and prescribes PKCE as the
actual protection — which :mod:`src.auth.google_auth` enables. What genuinely
must stay secret is the user's *token*, and that lives in the OS keyring.

The values are injected at build time rather than committed, so a fork of this
repository gets no working client and must supply its own. That keeps the
project's Google quota and verification status tied to whoever built the binary.
"""
from __future__ import annotations

import json
import logging
import os

from src import config

log = logging.getLogger(__name__)

#: Overwritten by scripts/build_desktop.py at packaging time. Empty here on
#: purpose: running from source uses your own client, not the shipped one.
EMBEDDED_CLIENT_ID = ""
EMBEDDED_CLIENT_SECRET = ""

#: Google's fixed endpoints. Only the client identity varies.
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _from_environment() -> tuple[str, str]:
    """Client identity from the environment, for testing a build locally."""
    return (
        os.getenv("GOOGLE_CLIENT_ID", ""),
        os.getenv("GOOGLE_CLIENT_SECRET", ""),
    )


def embedded_client() -> dict | None:
    """The shipped client as an ``InstalledAppFlow`` config, or None.

    None means this build has no embedded client, which is the normal state when
    running from source — the user supplies their own JSON instead.
    """
    client_id, client_secret = EMBEDDED_CLIENT_ID, EMBEDDED_CLIENT_SECRET
    if not client_id:
        client_id, client_secret = _from_environment()
    if not client_id:
        return None

    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            # Loopback redirect: the flow listens on a local port Google sends
            # the code back to. No hosted callback, so nothing to run server-side.
            "redirect_uris": ["http://localhost"],
        }
    }


def user_client() -> dict | None:
    """The user's own downloaded client JSON, if they have supplied one.

    Takes precedence over the embedded client: someone who went to the trouble of
    creating a Cloud project wants their own quota and their own scopes — it is
    also the only way to reach the Gmail API path.
    """
    path = config.GOOGLE_CLIENT_SECRETS
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("Google client file at %s is unreadable; ignoring it.", path)
        return None
    if "installed" not in data and "web" not in data:
        log.warning(
            "Google client file at %s is not a Desktop app credential.", path
        )
        return None
    return data


def client_config() -> dict | None:
    """Whichever client should be used, user-supplied first."""
    return user_client() or embedded_client()


def is_user_supplied() -> bool:
    """Whether the active client belongs to the user rather than the build.

    Gates the Gmail API path: the restricted ``gmail.readonly`` scope is only
    offered to people using their own Cloud project, because shipping it in the
    public client would require an annual third-party security assessment.
    """
    return user_client() is not None
