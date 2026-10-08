"""Sign in with Google, and keep the resulting credentials usable.

Sign-in asks for **identity only**. That is a deliberate cost decision, not
minimalism for its own sake: Google prices scope tiers very differently, and
``gmail.readonly`` is *restricted*, meaning an app offering it publicly needs an
annual third-party security assessment. Identity scopes are *basic* and need no
verification at all, so an app that signs users in and writes ``.ics`` files can
be handed to anyone today.

Anything beyond identity is therefore requested later and separately, by
incremental authorization, and only when the user asks for the feature:

``calendar.events``  when the user chooses Google Calendar as their output
``gmail.readonly``   only for users running their own Cloud project

Because a token may now carry any of three scope sets, nothing here may assume
which one it has — :func:`credentials` reads the granted scopes back off the
stored token rather than asserting a fixed list.

Where the token lives matters. The refresh token is a long-lived credential, so
it goes into the OS keyring alongside the IMAP password rather than into a JSON
file next to the database (PROJECT_PLAN.md §16).
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
    """Who is signed in, and what they actually granted.

    The two properties are load-bearing rather than cosmetic: signing in no
    longer implies either capability, so every caller that reaches a Google API
    must check first or it will get a 403 on a token that is otherwise perfectly
    valid.
    """

    email: str
    scopes: tuple[str, ...]
    #: Google refused to renew the sign-in; only signing in again fixes it.
    expired: bool = False

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
#: Set beside the credentials once Google refuses to renew them, so the setup
#: screen can say "sign in again" without asking Google on every view, and jobs
#: fail at once instead of each asking Google again. Signing in stores a fresh
#: token, which does not carry it.
EXPIRED_KEY = "ect_needs_sign_in"

#: Where the user signs in again. Every message about an expired sign-in ends
#: with this, so it always says what to do.
SIGN_IN_AGAIN = "Sign in again under Settings → Integrations."


def needs_sign_in(exc: BaseException) -> bool:
    """Whether a failure means the user has to sign in again.

    Google refusing to renew a sign-in — revoked, or expired: a Cloud project
    in "Testing" mode has its sign-ins expire after seven days — fails the same
    way however often it is retried. A network failure on the way to Google
    does not, so it is not counted.
    """
    from google.auth.exceptions import RefreshError, TransportError

    if isinstance(exc, RefreshError):
        return True
    if isinstance(exc, GoogleAuthError):
        return not isinstance(exc.__cause__, TransportError)
    return False


def _mark_expired() -> None:
    data = stored_token()
    if data is not None and not data.get(EXPIRED_KEY):
        data[EXPIRED_KEY] = True
        keyring.set_password(config.KEYRING_SERVICE, KEYRING_ACCOUNT, json.dumps(data))
        log.warning("Google refused to renew the sign-in. %s", SIGN_IN_AGAIN)


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


def granted_scopes() -> tuple[str, ...]:
    """The scopes the stored token actually carries.

    Read from the token rather than from config, because what the user consented
    to and what this build would like to have are now routinely different.
    """
    data = stored_token() or {}
    return tuple(data.get("scopes") or ())


def has_scope(suffix: str) -> bool:
    """Whether a scope ending in ``suffix`` was granted."""
    return any(scope.endswith(suffix) for scope in granted_scopes())


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
            "Not signed in to Google. Use 'Sign in with Google' under Settings → Integrations."
        )
    if data.get(EXPIRED_KEY):
        raise GoogleAuthError(f"The Google sign-in has expired. {SIGN_IN_AGAIN}")

    # The scopes come from the token, not from config. Passing a fixed list here
    # made every identity-only or calendar-only sign-in look invalid, because the
    # library treats a requested scope the token lacks as a mismatch.
    scopes = list(data.get("scopes") or config.GOOGLE_IDENTITY_SCOPES)
    creds = Credentials.from_authorized_user_info(data, scopes)

    if refresh and not creds.valid:
        if not creds.refresh_token:
            _mark_expired()
            raise GoogleAuthError(
                f"The Google sign-in has expired and cannot renew itself. {SIGN_IN_AGAIN}"
            )
        try:
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as text
            if needs_sign_in(exc):
                _mark_expired()
                raise GoogleAuthError(
                    f"Google would not renew the sign-in ({exc}). {SIGN_IN_AGAIN}"
                ) from exc
            raise GoogleAuthError(f"Could not reach Google to renew the sign-in: {exc}") from exc
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
        expired=bool(data.get(EXPIRED_KEY)),
    )


# --- Sign-in ---------------------------------------------------------------

def client_secrets_present() -> bool:
    """Whether *any* usable OAuth client exists.

    True for a packaged build carrying the embedded client, as well as for a user
    who supplied their own JSON — sign-in is possible either way.
    """
    from src.auth import google_client

    return google_client.client_config() is not None


def sign_in(scopes: tuple[str, ...] | None = None) -> GoogleAccount:
    """Run the consent flow in the user's browser and store the result.

    Defaults to identity only. Ask for more by passing ``scopes`` — for example
    :data:`config.GOOGLE_CALENDAR_SCOPES` when the user opts into Google
    Calendar. Previously granted scopes are carried forward, so enabling a second
    feature never silently revokes the first.

    Blocks until the user finishes (or closes) the consent screen, so callers on
    a UI thread should run it in the background.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    from src.auth import google_client

    client_config = google_client.client_config()
    if client_config is None:
        raise GoogleAuthError(
            "This build has no Google client configured. Create an OAuth client "
            "ID of type 'Desktop app' in your own Google Cloud project, download "
            f"the JSON, and save it as {config.GOOGLE_CLIENT_SECRETS}. "
            "See docs/google-setup.md."
        )

    requested = set(scopes or config.GOOGLE_IDENTITY_SCOPES)
    # Incremental authorization: keep what the user already agreed to. Google
    # would otherwise issue a token carrying only the new scope, quietly breaking
    # whichever feature was set up first.
    requested.update(granted_scopes())

    flow = InstalledAppFlow.from_client_config(
        client_config,
        sorted(requested),
        # PKCE. The client secret inside a distributed binary is not secret
        # (RFC 8252 §8.5), so the proof key is what actually stops an intercepted
        # redirect from being exchanged for a token. This is the library default
        # as of google-auth-oauthlib 1.4, but it is stated rather than assumed:
        # it is the security property the whole embedded-client design rests on.
        autogenerate_code_verifier=True,
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


def grant_calendar_access() -> GoogleAccount:
    """Ask for calendar permission on top of an existing sign-in."""
    return sign_in(scopes=config.GOOGLE_CALENDAR_SCOPES)


#: OpenID Connect's standard "who is this" endpoint. Covered by the identity
#: scopes, so it answers for every sign-in — unlike the Gmail profile call this
#: used to make, which needed the restricted mail scope and therefore returned
#: nothing for the now-default identity-only user.
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def signed_in_email(creds=None) -> str:
    """The address the credentials belong to.

    Falls through three sources, cheapest first. The address is a nicety for the
    setup screen, so every failure here is swallowed rather than raised.
    """
    creds = creds or credentials()

    claims = getattr(creds, "id_token", None)
    if isinstance(claims, dict) and claims.get("email"):
        return claims["email"]

    try:
        from google.auth.transport.requests import AuthorizedSession

        response = AuthorizedSession(creds).get(USERINFO_URL, timeout=10)
        if response.ok:
            email = response.json().get("email", "")
            if email:
                return email
    except Exception:  # noqa: BLE001
        log.debug("Could not read the OpenID userinfo address.", exc_info=True)

    # Last resort, and only meaningful for a token that carries the mail scope.
    if not has_scope("gmail.readonly"):
        return ""
    try:
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress", "")
    except Exception:  # noqa: BLE001 - the address is a nicety, not a blocker
        log.debug("Could not read the Gmail profile address.", exc_info=True)
        return ""
