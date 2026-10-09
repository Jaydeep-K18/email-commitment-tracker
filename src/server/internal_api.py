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
from src.storage.accounts import mailbox_owner_id
from src.storage.database import session_scope
from src.storage.tenancy import acting_as, current_user_id

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


def require_user() -> int:
    """The account the Express server is acting for (see acting_user.py)."""
    user_id = current_user_id()
    if user_id is None:
        raise HTTPException(400, "X-User-Id must name an active account")
    return user_id


def holds_local_credentials(user_id: int) -> bool:
    with acting_as(None), session_scope() as session:
        return user_id == mailbox_owner_id(session)


def require_credentials_owner(user_id: int = Depends(require_user)) -> int:
    """Mail and Google sign-ins on this machine are one account's, for now.

    Letting anyone else change them would replace that person's sign-in with
    theirs; letting anyone else read them would show that person's address.
    """
    if not holds_local_credentials(user_id):
        raise HTTPException(403, "Connecting a mailbox is not available for this account yet.")
    return user_id


router = APIRouter(prefix="/internal", dependencies=[Depends(require_internal_token)])


class Mailbox(BaseModel):
    address: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


@router.get("/setup/status")
def setup_status(user_id: int = Depends(require_user)) -> dict:
    """Everything the first-run screen shows, in one call."""
    from src.extraction.ollama_client import OllamaClient

    health = OllamaClient().health()
    ollama = {
        "running": health.running,
        "modelPresent": health.model_present,
        "model": config.OLLAMA_MODEL,
        "problem": health.problem,
    }
    if not holds_local_credentials(user_id):
        return {
            "ollama": ollama,
            "mailbox": {"address": None, "connected": False, "viaGmailApi": False},
            "google": {"signedIn": False, "expired": False, "email": None, "calendar": False,
                       "clientConfigured": google_auth.client_secrets_present()},
            "complete": False,
            "missing": "mailbox",
        }

    state = first_run.setup_state()
    account = google_auth.account()
    return {
        "ollama": ollama,
        "mailbox": {
            "address": config.IMAP_USER or None,
            "connected": state.mailbox_ready,
            "viaGmailApi": state.google_mail_access,
        },
        "google": {
            "signedIn": account is not None,
            "expired": bool(account and account.expired),
            "email": account.email if account else None,
            "calendar": bool(account and account.has_calendar),
            "clientConfigured": google_auth.client_secrets_present(),
        },
        "complete": state.complete,
        "missing": state.missing,
    }


@router.post("/setup/mailbox/test", dependencies=[Depends(require_credentials_owner)])
def test_mailbox(mailbox: Mailbox) -> dict:
    ok, message = first_run.check_connection(mailbox.address, mailbox.password)
    return {"ok": ok, "message": message}


@router.post("/setup/mailbox", dependencies=[Depends(require_credentials_owner)])
def save_mailbox(mailbox: Mailbox) -> dict:
    """Test, and only if the login works, store the address and the password."""
    ok, message = first_run.check_connection(mailbox.address, mailbox.password)
    if ok:
        first_run.save_email_address(mailbox.address)
        first_run.save_password(mailbox.address, mailbox.password)
    return {"ok": ok, "message": message}


@router.post("/setup/google/sign-in", dependencies=[Depends(require_credentials_owner)])
def google_sign_in() -> dict:
    """Run Google's consent flow in a browser tab on this machine.

    Blocks until the user finishes or abandons it. FastAPI runs a plain ``def``
    route in a worker thread, so this does not hold up other requests.

    Signing in again after Google ended a sign-in also catches up: the mail
    check and calendar update that failed meanwhile are queued straight away,
    rather than waiting for the user to think of pressing Sync now.
    """
    previous = google_auth.account()
    try:
        account = google_auth.sign_in()
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        raise HTTPException(400, f"Sign-in did not complete: {exc}") from exc
    resumed = bool(previous and previous.expired)
    if resumed:
        from src.jobs.queue import enqueue_unless_active

        with session_scope() as session:
            enqueue_unless_active(session, "fetch_mailbox")
            enqueue_unless_active(session, "publish_calendar")
    return {"email": account.email, "calendar": account.has_calendar, "resumed": resumed}


@router.post("/setup/google/calendar", dependencies=[Depends(require_credentials_owner)])
def google_calendar_access() -> dict:
    try:
        account = google_auth.grant_calendar_access()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Calendar access was not granted: {exc}") from exc
    return {"email": account.email, "calendar": account.has_calendar}


@router.delete("/setup/google", dependencies=[Depends(require_credentials_owner)])
def google_disconnect() -> dict:
    google_auth.clear_token()
    return {"signedIn": False}


@router.get("/extension-token", dependencies=[Depends(require_credentials_owner)])
def extension_token() -> dict:
    """The Gmail panel's token, shown in Settings → Integrations to paste in."""
    return {"token": api_token.get_or_create_token()}


@router.post("/extension-token/rotate", dependencies=[Depends(require_credentials_owner)])
def rotate_extension_token() -> dict:
    return {"token": api_token.rotate_token()}
