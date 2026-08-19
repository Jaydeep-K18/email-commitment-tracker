"""Tests for the changes that make this installable by someone who is not me.

Three things are under test, and all three are failure modes that only appear on
a machine other than the author's:

1. **Scopes are no longer all-or-nothing.** Sign-in asks for identity only, so
   every path that reaches a Google API must check what was actually granted
   rather than assuming a sign-in implies everything.
2. **The app is not locked to Google Calendar.** A user who never grants calendar
   access must still get working output.
3. **The local model is a requirement, not an assumption.** Setup previously
   reported itself ready with no model installed, which produced a silent
   failure at extraction time.
"""
from __future__ import annotations

import pytest

from src import config, first_run
from src.auth import google_auth, google_client
from src.collection import gmail_fetcher
from src.extraction.ollama_client import OllamaClient, OllamaHealth
from src.sync import google_calendar


@pytest.fixture()
def memory_keyring(monkeypatch):
    """An in-process keyring, so tests never touch the real credential store."""
    store: dict[tuple[str, str], str] = {}

    monkeypatch.setattr(
        google_auth.keyring, "set_password",
        lambda service, account, value: store.__setitem__((service, account), value),
    )
    monkeypatch.setattr(
        google_auth.keyring, "get_password",
        lambda service, account: store.get((service, account)),
    )
    return store


def signed_in_with(monkeypatch, scopes):
    monkeypatch.setattr(
        google_auth, "account",
        lambda: google_auth.GoogleAccount(email="me@example.com", scopes=tuple(scopes)),
    )


# --- Scope separation ------------------------------------------------------

def test_the_three_scope_sets_nest():
    """Each tier builds on the one below, so asking for calendar never silently
    drops the identity scopes the app needs to know who signed in."""
    assert set(config.GOOGLE_IDENTITY_SCOPES) <= set(config.GOOGLE_CALENDAR_SCOPES)
    assert set(config.GOOGLE_CALENDAR_SCOPES) <= set(config.GOOGLE_MAIL_SCOPES)


def test_sign_in_does_not_ask_for_mail_or_calendar_by_default():
    """The whole cost argument rests on this: identity scopes are 'basic' and
    need no verification, while gmail.readonly is 'restricted' and would oblige
    an annual third-party security assessment."""
    requested = " ".join(config.GOOGLE_IDENTITY_SCOPES)
    assert "gmail" not in requested
    assert "calendar" not in requested


def test_an_identity_only_token_is_still_usable(memory_keyring, monkeypatch):
    """credentials() used to assert a fixed scope list, which made every
    identity-only sign-in look invalid."""
    class FakeCreds:
        valid = True
        def to_json(self):
            import json
            return json.dumps({
                "refresh_token": "r",
                "token": "t",
                "scopes": list(config.GOOGLE_IDENTITY_SCOPES),
            })

    google_auth.store_token(FakeCreds(), email="me@example.com")

    captured = {}

    class FakeCredentials:
        valid = True
        @classmethod
        def from_authorized_user_info(cls, data, scopes):
            captured["scopes"] = scopes
            return cls()

    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials", FakeCredentials, raising=False
    )
    google_auth.credentials(refresh=False)

    assert captured["scopes"] == list(config.GOOGLE_IDENTITY_SCOPES)


def test_granted_scopes_are_read_from_the_token(memory_keyring):
    import json

    class FakeCreds:
        def to_json(self):
            return json.dumps({"scopes": list(config.GOOGLE_CALENDAR_SCOPES)})

    google_auth.store_token(FakeCreds())

    assert google_auth.has_scope("calendar.events") is True
    assert google_auth.has_scope("gmail.readonly") is False


# --- Nothing reaches an API it was not granted -----------------------------

def test_gmail_is_not_used_without_the_mail_scope(monkeypatch):
    """Otherwise a calendar-only user gets a 403 instead of falling back to
    IMAP, which is the path that actually works for them."""
    signed_in_with(monkeypatch, config.GOOGLE_CALENDAR_SCOPES)
    assert gmail_fetcher.is_available() is False


def test_gmail_is_used_when_the_scope_is_present(monkeypatch):
    signed_in_with(monkeypatch, config.GOOGLE_MAIL_SCOPES)
    assert gmail_fetcher.is_available() is True


def test_google_calendar_is_not_pushed_to_without_the_scope(monkeypatch):
    signed_in_with(monkeypatch, config.GOOGLE_IDENTITY_SCOPES)
    assert google_calendar.is_available() is False


def test_signing_out_disables_both(monkeypatch):
    monkeypatch.setattr(google_auth, "account", lambda: None)
    assert gmail_fetcher.is_available() is False
    assert google_calendar.is_available() is False


# --- Not locked to Google Calendar -----------------------------------------

def test_granting_calendar_access_is_enough_to_enable_the_push(monkeypatch):
    """Consenting is the opt-in. Requiring a second setting as well would strand
    anyone who granted access and then wondered why nothing appeared."""
    signed_in_with(monkeypatch, config.GOOGLE_CALENDAR_SCOPES)
    monkeypatch.setattr(config, "CALENDAR_TARGET", "")

    assert google_calendar.is_available() is True


def test_choosing_another_calendar_app_turns_the_push_off(monkeypatch):
    """A user who picked Outlook should not keep getting Google events, and
    should not have to revoke the permission to stop them."""
    signed_in_with(monkeypatch, config.GOOGLE_CALENDAR_SCOPES)
    monkeypatch.setattr(config, "CALENDAR_TARGET", config.CALENDAR_TARGET_ICS)

    assert google_calendar.is_available() is False


def test_the_ics_file_is_written_whatever_the_target(tmp_path, monkeypatch):
    """The vendor-neutral output must never depend on a Google decision."""
    from datetime import datetime

    from src.storage.models import Commitment
    from src.sync import ics_builder

    monkeypatch.setattr(config, "CALENDAR_TARGET", config.CALENDAR_TARGET_ICS)
    target = tmp_path / "calendar.ics"
    commitment = Commitment(
        id=1, email_id=None, type="deadline_on_you", subject="Send the report",
        deadline=datetime(2026, 9, 1, 17, 0), evidence_quote="q", confidence=0.9,
    )

    written = ics_builder.write_ics_file([commitment], path=target)

    assert written > 0
    text = target.read_text(encoding="utf-8")
    assert "BEGIN:VCALENDAR" in text
    assert "Send the report" in text


# --- The embedded client ---------------------------------------------------

def test_running_from_source_has_no_embedded_client(monkeypatch):
    """The shipped client is injected at build time, never committed, so a fork
    of the repo cannot borrow this project's Google quota or verification."""
    monkeypatch.setattr(google_client, "EMBEDDED_CLIENT_ID", "")
    monkeypatch.setattr(google_client, "EMBEDDED_CLIENT_SECRET", "")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)

    assert google_client.embedded_client() is None


def test_an_embedded_client_is_shaped_for_an_installed_app(monkeypatch):
    monkeypatch.setattr(google_client, "EMBEDDED_CLIENT_ID", "abc.apps.googleusercontent.com")
    monkeypatch.setattr(google_client, "EMBEDDED_CLIENT_SECRET", "shh")

    config_dict = google_client.embedded_client()

    assert "installed" in config_dict, "must be an installed-app client, not web"
    assert config_dict["installed"]["redirect_uris"] == ["http://localhost"]


def test_a_user_supplied_client_beats_the_embedded_one(tmp_path, monkeypatch):
    """Someone who made their own Cloud project wants their own quota — and it
    is the only way to reach the Gmail API path."""
    import json

    path = tmp_path / "google_client_secret.json"
    path.write_text(json.dumps({"installed": {"client_id": "mine"}}), encoding="utf-8")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRETS", path)
    monkeypatch.setattr(google_client, "EMBEDDED_CLIENT_ID", "shipped")

    assert google_client.is_user_supplied() is True
    assert google_client.client_config()["installed"]["client_id"] == "mine"


def test_a_corrupt_client_file_is_ignored_rather_than_fatal(tmp_path, monkeypatch):
    path = tmp_path / "google_client_secret.json"
    path.write_text("not json at all", encoding="utf-8")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRETS", path)
    monkeypatch.setattr(google_client, "EMBEDDED_CLIENT_ID", "")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)

    assert google_client.user_client() is None
    assert google_client.client_config() is None


# --- Ollama readiness ------------------------------------------------------

def test_health_separates_not_running_from_no_model(monkeypatch):
    """Two different problems with two different fixes; collapsing them into
    'not ready' would tell a user to install software they already have."""
    monkeypatch.setattr(OllamaClient, "is_available", lambda self: True)
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["phi3:latest"])

    health = OllamaClient().health()
    assert health.running is True
    assert health.model_present is False
    assert "model" in health.problem


def test_health_accepts_a_model_pulled_without_a_tag(monkeypatch):
    """The README says `ollama pull llama3.2`; Ollama then reports it as
    `llama3.2:latest`. Both must count as installed."""
    monkeypatch.setattr(config, "OLLAMA_MODEL", "llama3.2:latest")
    monkeypatch.setattr(OllamaClient, "is_available", lambda self: True)
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["llama3.2"])

    assert OllamaClient(model="llama3.2:latest").health().model_present is True


def test_health_does_not_raise_when_ollama_is_absent(monkeypatch):
    """ensure_ready() raises by design; a status panel needs to render instead."""
    monkeypatch.setattr(OllamaClient, "is_available", lambda self: False)

    health = OllamaClient().health()
    assert health.ready is False
    assert "not running" in health.problem


def test_setup_is_incomplete_without_a_model(monkeypatch):
    monkeypatch.setattr(
        OllamaClient, "health",
        lambda self: OllamaHealth(running=False, model_present=False, host="stub"),
    )
    monkeypatch.setattr(google_auth, "account", lambda: None)
    monkeypatch.setattr(first_run, "password_is_stored", lambda *_a: True)
    monkeypatch.setattr(config, "IMAP_USER", "someone@example.com")

    assert first_run.setup_state().complete is False


def test_a_fresh_install_reports_every_step_as_outstanding(monkeypatch):
    """What a stranger sees on first launch."""
    monkeypatch.setattr(
        OllamaClient, "health",
        lambda self: OllamaHealth(running=False, model_present=False, host="stub"),
    )
    monkeypatch.setattr(google_auth, "account", lambda: None)
    monkeypatch.setattr(first_run, "password_is_stored", lambda *_a: False)
    monkeypatch.setattr(config, "IMAP_USER", "")

    state = first_run.setup_state()

    assert state.model_ready is False
    assert state.mailbox_ready is False
    assert state.signed_in_with_google is False
    assert state.complete is False
    assert state.missing   # must name something actionable, not an empty string
