"""The worker's private API, called only by the Express server.

Some setup steps can only happen on the machine the worker runs on: checking
the local Ollama, testing an IMAP login, storing the mailbox password in the OS
keyring, and running Google's sign-in, which opens a browser tab on this
desktop. The Express server owns the UI and the user's session, so it forwards
those actions here.

Two guards, either of which would stop a stranger on their own:

* The server binds to ``127.0.0.1`` (``SERVER_HOST``), so nothing off this
  machine can connect at all.
* Every route requires ``X-Internal-Token`` to equal ``INTERNAL_API_TOKEN``,
  compared in constant time. Loopback alone is not a boundary — any web page
  the user has open can make requests to 127.0.0.1 — and this token is what
  stops that. With no token configured the routes refuse to run at all.

Nothing here ever returns a secret: the mailbox password goes into the keyring
and never comes back out.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from src import config, first_run
from src.auth import google_auth
from src.server import api_token

log = logging.getLogger(__name__)

TOKEN_HEADER = "X-Internal-Token"


def require_internal_token(
    supplied: str | None = Header(default=None, alias=TOKEN_HEADER),
) -> None:
    expected = config.INTERNAL_API_TOKEN
    if not expected:
        raise HTTPException(503, "INTERNAL_API_TOKEN is not configured")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "invalid internal token")


router = APIRouter(prefix="/internal", dependencies=[Depends(require_internal_token)])


class Mailbox(BaseModel):
    address: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


@router.get("/setup/status")
def setup_status() -> dict:
    """Everything the first-run screen shows, in one call."""
    from src.extraction.ollama_client import OllamaClient

    state = first_run.setup_state()
    health = OllamaClient().health()
    account = google_auth.account()
    return {
        "ollama": {
            "running": health.running,
            "modelPresent": health.model_present,
            "model": config.OLLAMA_MODEL,
            "problem": health.problem,
        },
        "mailbox": {
            "address": config.IMAP_USER or None,
            "connected": state.mailbox_ready,
            "viaGmailApi": state.google_mail_access,
        },
        "google": {
            "signedIn": account is not None,
            "email": account.email if account else None,
            "calendar": bool(account and account.has_calendar),
            "clientConfigured": google_auth.client_secrets_present(),
        },
        "complete": state.complete,
        "missing": state.missing,
    }


@router.post("/setup/mailbox/test")
def test_mailbox(mailbox: Mailbox) -> dict:
    ok, message = first_run.check_connection(mailbox.address, mailbox.password)
    return {"ok": ok, "message": message}


@router.post("/setup/mailbox")
def save_mailbox(mailbox: Mailbox) -> dict:
    """Test, and only if the login works, store the address and the password."""
    ok, message = first_run.check_connection(mailbox.address, mailbox.password)
    if ok:
        first_run.save_email_address(mailbox.address)
        first_run.save_password(mailbox.address, mailbox.password)
    return {"ok": ok, "message": message}


@router.post("/setup/google/sign-in")
def google_sign_in() -> dict:
    """Run Google's consent flow in a browser tab on this machine.

    Blocks until the user finishes or abandons it. FastAPI runs a plain ``def``
    route in a worker thread, so this does not hold up other requests.
    """
    try:
        account = google_auth.sign_in()
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        raise HTTPException(400, f"Sign-in did not complete: {exc}") from exc
    return {"email": account.email, "calendar": account.has_calendar}


@router.post("/setup/google/calendar")
def google_calendar_access() -> dict:
    try:
        account = google_auth.grant_calendar_access()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Calendar access was not granted: {exc}") from exc
    return {"email": account.email, "calendar": account.has_calendar}


@router.delete("/setup/google")
def google_disconnect() -> dict:
    google_auth.clear_token()
    return {"signedIn": False}


@router.get("/extension-token")
def extension_token() -> dict:
    """The Gmail panel's token, shown in Settings → Integrations to paste in."""
    return {"token": api_token.get_or_create_token()}


@router.post("/extension-token/rotate")
def rotate_extension_token() -> dict:
    return {"token": api_token.rotate_token()}
