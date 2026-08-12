"""Sign in with Google, and keep the resulting credentials usable.

The app needs two things from Google: permission to read mail, and permission to
write calendar events. Both come from one consent, so the user signs in once
rather than hunting through account settings for a 16-character app password.

Where the token lives matters. The refresh token is a long-lived credential that
can re-obtain access to the user's mailbox, so it goes into the OS keyring
alongside the IMAP password rather than into a JSON file next to the database
(PROJECT_PLAN.md §16). Only the non-secret parts of the client configuration are
read from disk.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import keyring

from src import config

log = logging.getLogger(__name__)

#: Keyring account under which the serialised credentials are stored. A fixed
#: string rather than the address, because the address is only known *after*
#: signing in — and the token is what tells us which account it belongs to.
KEYRING_ACCOUNT = "google-oauth-token"


class GoogleAuthError(RuntimeError):
    """Raised when sign-in is impossible or the stored token is unusable."""


@dataclass(frozen=True)
class GoogleAccount:
    """Who is signed in, for display on the setup screen."""

    email: str
    scopes: tuple[str, ...]

    @property
    def has_calendar(self) -> bool:
        return any(scope.endswith("calendar.events") for scope in self.scopes)

    @property
    def has_mail(self) -> bool:
        return any(scope.endswith("gmail.readonly") for scope in self.scopes)


# --- Token storage ---------------------------------------------------------

#: Extra key stored beside the credentials. ``Credentials.to_json()`` has no
#: reliable address field, and the setup screen should be able to say which
#: account is connected without a network round trip on every rerun.
EMAIL_KEY = "ect_email"


def store_token(credentials, email: str | None = None) -> None:
    """Persist credentials to the keyring as JSON, keeping any known address."""
    data = json.loads(credentials.to_json())
    if email:
        data[EMAIL_KEY] = email
    else:
        # A refresh must not erase the address recorded at sign-in.
        previous = stored_token() or {}
        if previous.get(EMAIL_KEY):
            data[EMAIL_KEY] = previous[EMAIL_KEY]
    keyring.set_password(
        config.KEYRING_SERVICE, KEYRING_ACCOUNT, json.dumps(data)
    )


def clear_token() -> None:
    """Forget the signed-in account (the 'disconnect' button)."""
    try:
        keyring.delete_password(config.KEYRING_SERVICE, KEYRING_ACCOUNT)
    except keyring.errors.PasswordDeleteError:
        pass  # Nothing stored is the desired end state either way.


def stored_token() -> dict | None:
    """The raw stored credentials, or None when not signed in."""
    try:
        raw = keyring.get_password(config.KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception:  # noqa: BLE001 - a locked keyring means "not signed in"
        log.debug("Keyring unavailable.", exc_info=True)
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # A corrupted entry should not wedge the app into an unusable state.
        log.warning("Stored Google token is not valid JSON; ignoring it.")
        return None


def is_signed_in() -> bool:
    """Whether a token exists. Does not prove it still works."""
    return stored_token() is not None


# --- Credentials -----------------------------------------------------------

def credentials(*, refresh: bool = True):
    """Return usable credentials, refreshing them if they have expired.

    A refreshed access token is written back to the keyring so the next process
    starts from the newer one instead of refreshing again.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    data = stored_token()
    if data is None:
        raise GoogleAuthError(
            "Not signed in to Google. Open the dashboard and use "
            "'Sign in with Google' on the setup page."
        )

    creds = Credentials.from_authorized_user_info(data, list(config.GOOGLE_SCOPES))

    if refresh and not creds.valid:
        if not creds.refresh_token:
            raise GoogleAuthError(
                "The Google sign-in has expired and cannot renew itself. "
                "Sign in again from the setup page."
            )
        try:
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as text
            raise GoogleAuthError(
                f"Could not refresh the Google sign-in: {exc}. "
                "Sign in again from the setup page."
            ) from exc
        store_token(creds)
        log.info("Refreshed the Google access token.")

    return creds


def account() -> GoogleAccount | None:
    """Who is signed in, read from the stored token without any network call."""
    data = stored_token()
    if data is None:
        return None
    return GoogleAccount(
        email=data.get(EMAIL_KEY, ""),
        scopes=tuple(data.get("scopes") or ()),
    )


# --- Sign-in ---------------------------------------------------------------

def client_secrets_present() -> bool:
    """Whether the user has supplied their own OAuth client configuration."""
    return config.GOOGLE_CLIENT_SECRETS.exists()


def sign_in() -> GoogleAccount:
    """Run the consent flow in the user's browser and store the result.

    Blocks until the user finishes (or closes) the Google consent screen, so
    callers on a UI thread should run it in the background.
    """
    if not client_secrets_present():
        raise GoogleAuthError(
            f"No Google client configuration found at "
            f"{config.GOOGLE_CLIENT_SECRETS}. Create an OAuth client ID of type "
            "'Desktop app' in your own Google Cloud project, download the JSON, "
            "and save it there. See the README."
        )

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(config.GOOGLE_CLIENT_SECRETS), list(config.GOOGLE_SCOPES)
    )
    # port=0 asks the OS for a free port, so the redirect listener cannot
    # collide with the dashboard (8501) or the calendar server (8765).
    creds = flow.run_local_server(
        port=config.GOOGLE_OAUTH_PORT,
        prompt="consent",          # always return a refresh token
        access_type="offline",     # ...which is what makes it work unattended
        authorization_prompt_message="Opening your browser to sign in to Google…",
        success_message=(
            "Signed in. You can close this tab and return to the tracker."
        ),
    )
    email = signed_in_email(creds)
    store_token(creds, email=email)
    log.info("Signed in to Google as %s", email or "(unknown address)")
    return GoogleAccount(email=email, scopes=tuple(creds.scopes or ()))


def signed_in_email(creds=None) -> str:
    """The address the credentials belong to.

    Read from the id_token when one is present; otherwise asked of Gmail
    directly, since the token itself does not have to carry an address.
    """
    creds = creds or credentials()

    claims = getattr(creds, "id_token", None)
    if isinstance(claims, dict) and claims.get("email"):
        return claims["email"]

    try:
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress", "")
    except Exception:  # noqa: BLE001 - the address is a nicety, not a blocker
        log.debug("Could not read the Gmail profile address.", exc_info=True)
        return ""
