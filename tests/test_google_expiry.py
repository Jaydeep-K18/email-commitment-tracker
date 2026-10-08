"""When Google ends a sign-in.

Google refuses to renew a sign-in that was revoked or has expired — for a Cloud
project in "Testing" mode, seven days after it was made. Nothing but signing in
again fixes that, so the app has to: say so plainly, stop retrying work that
cannot succeed, not blame the wrong thing (an unconfigured IMAP fallback), and
pick up again by itself once the user has signed in.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError, TransportError
from sqlalchemy import select

from src import config
from src.auth import google_auth
from src.collection import email_fetcher, gmail_fetcher
from src.jobs import handlers
from src.jobs.handlers import JobContext, Services
from src.jobs.queue import PermanentJobError
from src.server.calendar_server import app
from src.storage.database import session_scope
from src.storage.models import Job
from src.sync import google_calendar

TOKEN = "internal-test-token"


@pytest.fixture()
def keyring_store(monkeypatch):
    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(google_auth.keyring, "set_password", lambda s, a, v: store.__setitem__((s, a), v))
    monkeypatch.setattr(google_auth.keyring, "get_password", lambda s, a: store.get((s, a)))
    return store


def sign_in_stored(store, **extra):
    data = {"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly",
                       "https://www.googleapis.com/auth/calendar.events"],
            google_auth.EMAIL_KEY: "me@example.com", **extra}
    store[(config.KEYRING_SERVICE, google_auth.KEYRING_ACCOUNT)] = json.dumps(data)


class RefusingCredentials:
    """Expired access token; renewing it fails the way the test says."""

    refreshes = 0
    failure: Exception = RefreshError("invalid_grant: Bad Request")
    valid = False
    refresh_token = "r"

    @classmethod
    def from_authorized_user_info(cls, data, scopes):
        return cls()

    def refresh(self, request):
        type(self).refreshes += 1
        raise type(self).failure


@pytest.fixture()
def refusing(monkeypatch):
    RefusingCredentials.refreshes = 0
    RefusingCredentials.failure = RefreshError("invalid_grant: Bad Request")
    monkeypatch.setattr("google.oauth2.credentials.Credentials", RefusingCredentials)
    return RefusingCredentials


# --- Recognising it -------------------------------------------------------------

def test_a_refused_renewal_needs_a_new_sign_in_but_a_network_failure_does_not():
    assert google_auth.needs_sign_in(RefreshError("invalid_grant"))
    try:
        raise google_auth.GoogleAuthError("x") from RefreshError("invalid_grant")
    except google_auth.GoogleAuthError as wrapped:
        assert google_auth.needs_sign_in(wrapped)
    try:
        raise google_auth.GoogleAuthError("x") from TransportError("no route to host")
    except google_auth.GoogleAuthError as offline:
        assert not google_auth.needs_sign_in(offline)
    assert google_auth.needs_sign_in(google_auth.GoogleAuthError("Not signed in"))
    assert not google_auth.needs_sign_in(ConnectionError("reset"))


def test_the_wrapped_error_is_not_retried_but_a_network_failure_is():
    """The retry check used to look only for the library's RefreshError, which
    the app wraps — so an expired sign-in was retried five times per job."""
    try:
        raise google_auth.GoogleAuthError("x") from RefreshError("invalid_grant")
    except google_auth.GoogleAuthError as expired:
        assert not google_calendar.is_retryable(expired)
    try:
        raise google_auth.GoogleAuthError("x") from TransportError("offline")
    except google_auth.GoogleAuthError as offline:
        assert google_calendar.is_retryable(offline)


# --- Remembering it ---------------------------------------------------------------

def test_a_refused_renewal_is_remembered_and_says_what_to_do(keyring_store, refusing):
    sign_in_stored(keyring_store)

    with pytest.raises(google_auth.GoogleAuthError, match="Settings → Integrations"):
        google_auth.credentials()

    assert google_auth.account().expired
    assert google_auth.account().email == "me@example.com"


def test_once_known_expired_nothing_asks_google_again(keyring_store, refusing):
    sign_in_stored(keyring_store)
    for _ in range(3):
        with pytest.raises(google_auth.GoogleAuthError):
            google_auth.credentials()
    assert refusing.refreshes == 1


def test_being_offline_is_not_mistaken_for_an_expired_sign_in(keyring_store, refusing):
    sign_in_stored(keyring_store)
    refusing.failure = TransportError("no route to host")

    with pytest.raises(google_auth.GoogleAuthError, match="Could not reach Google"):
        google_auth.credentials()
    assert not google_auth.account().expired


def test_a_new_sign_in_clears_it(keyring_store):
    sign_in_stored(keyring_store, **{google_auth.EXPIRED_KEY: True})

    class Fresh:
        def to_json(self):
            return json.dumps({"token": "new", "refresh_token": "new", "scopes": []})

    google_auth.store_token(Fresh(), email="me@example.com")
    assert not google_auth.account().expired


# --- Acting on it -----------------------------------------------------------------

def test_mail_reports_the_google_problem_not_the_unset_imap_fallback(keyring_store, monkeypatch):
    """Before: 'No IMAP password found for you@example.com' — the placeholder
    address of a fallback the user never set up — hid the expired sign-in."""
    sign_in_stored(keyring_store, **{google_auth.EXPIRED_KEY: True})
    def no_imap_password():
        raise config.ConfigError("No IMAP password found in the keyring for 'you@example.com'.")

    monkeypatch.setattr(config, "get_imap_password", no_imap_password)

    with pytest.raises(google_auth.GoogleAuthError, match="expired"):
        email_fetcher.collect_raw_messages()


def test_a_configured_imap_fallback_still_takes_over(keyring_store, monkeypatch):
    sign_in_stored(keyring_store, **{google_auth.EXPIRED_KEY: True})
    monkeypatch.setattr(email_fetcher, "connect", lambda: "imap")
    monkeypatch.setattr(email_fetcher, "fetch_recent", lambda imap: [b"raw"])
    assert email_fetcher.collect_raw_messages() == [b"raw"]


def failing_fetch():
    try:
        raise google_auth.GoogleAuthError("The Google sign-in has expired. Sign in again.") from RefreshError("x")
    except google_auth.GoogleAuthError as exc:
        raise exc


def test_a_mail_check_stops_at_once_instead_of_retrying(monkeypatch):
    ctx = JobContext(1, 1, 5, Services(fetch=failing_fetch))
    with pytest.raises(PermanentJobError, match="expired"):
        handlers.run_job("fetch_mailbox", {}, ctx)


def test_a_calendar_push_stops_at_once_with_the_same_advice():
    try:
        raise google_auth.GoogleAuthError("expired. Sign in again under Settings → Integrations.") from RefreshError("x")
    except google_auth.GoogleAuthError as exc:
        failure = handlers._google_failure(exc)
    assert isinstance(failure, PermanentJobError)
    assert str(failure).endswith("Settings → Integrations.")


# --- Showing it, and recovering ------------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    from src.extraction.ollama_client import OllamaClient, OllamaHealth

    monkeypatch.setattr(config, "INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setattr(OllamaClient, "health", lambda self: OllamaHealth(running=True, model_present=True, host="stub"))
    return TestClient(app)


def test_setup_status_says_the_sign_in_expired(client, keyring_store):
    sign_in_stored(keyring_store, **{google_auth.EXPIRED_KEY: True})
    google = client.get("/internal/setup/status", headers={"X-Internal-Token": TOKEN}).json()["google"]
    assert google["signedIn"] is True
    assert google["expired"] is True


def test_signing_in_again_catches_up_on_mail_and_the_calendar(client, keyring_store, monkeypatch):
    sign_in_stored(keyring_store, **{google_auth.EXPIRED_KEY: True})

    def sign_in():
        sign_in_stored(keyring_store)
        return google_auth.account()

    monkeypatch.setattr(google_auth, "sign_in", sign_in)
    body = client.post("/internal/setup/google/sign-in", headers={"X-Internal-Token": TOKEN}).json()

    assert body["resumed"] is True
    with session_scope() as session:
        assert sorted(session.scalars(select(Job.type))) == ["fetch_mailbox", "publish_calendar"]


def test_a_first_sign_in_queues_nothing(client, keyring_store, monkeypatch):
    """During onboarding the calendar choice comes after signing in; publishing
    straight away would push events before the user has chosen where."""
    def sign_in():
        sign_in_stored(keyring_store)
        return google_auth.account()

    monkeypatch.setattr(google_auth, "sign_in", sign_in)
    body = client.post("/internal/setup/google/sign-in", headers={"X-Internal-Token": TOKEN}).json()

    assert body["resumed"] is False
    with session_scope() as session:
        assert list(session.scalars(select(Job.type))) == []
