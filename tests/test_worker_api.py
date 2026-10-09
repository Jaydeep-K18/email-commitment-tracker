"""v2 phase 4, worker side: the internal API, settings, VIP re-apply, retention."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from src import config, first_run
from src.auth import google_auth
from src.events import recorder
from src.extraction.ollama_client import OllamaClient, OllamaHealth
from src.jobs import handlers
from src.jobs.handlers import JobContext, Services
from src.server.calendar_server import app
from src.storage import database, user_settings
from src.storage.models import (
    Commitment, Event, Job, MetricSnapshot, RawEmail, Setting, VipContact, utcnow_naive,
)

TOKEN = "internal-test-token"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setattr(
        OllamaClient, "health",
        lambda self: OllamaHealth(running=True, model_present=True, host="stub"),
    )
    monkeypatch.setattr(google_auth, "account", lambda: None)
    return TestClient(app)


def auth(token=TOKEN) -> dict:
    return {"X-Internal-Token": token, "X-User-Id": "1"}


# --- The internal API -----------------------------------------------------------

def test_without_a_configured_token_the_internal_api_refuses_to_run(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_API_TOKEN", "")
    assert client.get("/internal/setup/status", headers=auth()).status_code == 503


@pytest.mark.parametrize("headers", [{}, auth("wrong-token")])
def test_the_internal_api_rejects_anyone_without_the_token(client, headers):
    """Loopback is not a boundary: any page in the browser can call 127.0.0.1."""
    assert client.get("/internal/setup/status", headers=headers).status_code == 401


def test_setup_status_reports_every_step(client):
    body = client.get("/internal/setup/status", headers=auth()).json()
    assert body["ollama"]["running"] is True
    assert body["google"]["signedIn"] is False
    assert set(body) == {"ollama", "mailbox", "google", "complete", "missing"}


def test_a_mailbox_is_only_saved_when_the_login_works(client, monkeypatch):
    saved = []
    monkeypatch.setattr(first_run, "check_connection", lambda a, p: (False, "Login rejected"))
    monkeypatch.setattr(first_run, "save_password", lambda a, p: saved.append(a))
    monkeypatch.setattr(first_run, "save_email_address", lambda a: saved.append(a))

    response = client.post(
        "/internal/setup/mailbox", headers=auth(),
        json={"address": "me@example.com", "password": "app-password"},
    )

    assert response.json() == {"ok": False, "message": "Login rejected"}
    assert saved == []


def test_the_password_is_never_echoed_back(client, monkeypatch):
    monkeypatch.setattr(first_run, "check_connection", lambda a, p: (True, "Connected"))
    monkeypatch.setattr(first_run, "save_password", lambda a, p: None)
    monkeypatch.setattr(first_run, "save_email_address", lambda a: None)

    response = client.post(
        "/internal/setup/mailbox", headers=auth(),
        json={"address": "me@example.com", "password": "s3cret-app-pass"},
    )
    assert "s3cret-app-pass" not in response.text


# --- Settings the worker honours ------------------------------------------------

def save_setting(section: str, value: dict) -> None:
    with database.session_scope() as s:
        s.merge(Setting(section=section, value=value))


def test_the_apps_calendar_choice_overrides_the_old_environment_setting(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_TARGET", "google")
    save_setting("calendar", {"target": "ics"})
    with database.session_scope() as s:
        assert user_settings.calendar_target(s) == "ics"


def test_with_no_saved_settings_the_environment_still_applies(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_TARGET", "ics")
    monkeypatch.setattr(config, "FETCH_INTERVAL_MINUTES", 20)
    with database.session_scope() as s:
        assert user_settings.calendar_target(s) == "ics"
        assert user_settings.fetch_interval_minutes(s) == 20


def test_a_saved_fetch_window_is_applied_before_fetching(monkeypatch):
    monkeypatch.setattr(config, "FETCH_LOOKBACK_DAYS", 7)
    save_setting("email", {"lookbackDays": 30, "maxPerFetch": 200})
    with database.session_scope() as s:
        user_settings.apply_fetch_settings(s)
    assert (config.FETCH_LOOKBACK_DAYS, config.FETCH_MAX_EMAILS) == (30, 200)


# --- Re-applying contact rules --------------------------------------------------

def ctx() -> JobContext:
    return JobContext(1, 1, 5, Services(fetch=lambda: None, sync_sent=lambda: 0))


def test_changing_a_contacts_tier_reaches_their_emails_and_commitments():
    with database.session_scope() as s:
        email = RawEmail(message_id="m1", sender_email="boss@acme.com", vip_tier="CRITICAL",
                         processed=True, subject="Q3")
        s.add(email)
        s.flush()
        s.add(Commitment(email_id=email.id, type="deadline_on_you", subject="Send Q3",
                         deadline=datetime(2026, 9, 1), evidence_quote="q", vip_tier="CRITICAL"))
        s.add(VipContact(match_value="acme.com", match_type="domain", tier="MONITOR"))

    result = handlers.apply_vip_rules({}, ctx())

    assert result["retagged"] == 1
    with database.session_scope() as s:
        assert s.scalars(select(RawEmail.vip_tier)).one() == "MONITOR"
        # The calendar policy reads the commitment's tier, so it must follow.
        assert s.scalars(select(Commitment.vip_tier)).one() == "MONITOR"
        assert s.scalar(select(Job.id).where(Job.type == "publish_calendar")) is not None


def test_a_hand_picked_category_survives_re_applying_rules():
    with database.session_scope() as s:
        s.add(RawEmail(message_id="m2", sender_email="news@shop.com", category="important",
                       category_source="user", subject="Sale"))
    handlers.apply_vip_rules({}, ctx())
    with database.session_scope() as s:
        assert s.scalars(select(RawEmail.category)).one() == "important"


# --- Retention ----------------------------------------------------------------------

def old_email(message_id: str, days_ago: int, **fields) -> int:
    with database.session_scope() as s:
        email = RawEmail(message_id=message_id, subject="x", body_text="body",
                         received_at=utcnow_naive() - timedelta(days=days_ago), **fields)
        s.add(email)
        s.flush()
        return email.id


def test_retention_deletes_old_mail_but_never_an_upcoming_commitments_source():
    save_setting("privacy", {"retentionDays": 30, "keepEmailBodies": True})
    stale = old_email("stale", days_ago=90)
    needed = old_email("needed", days_ago=90)
    recent = old_email("recent", days_ago=2)
    with database.session_scope() as s:
        s.add(Commitment(email_id=needed, type="deadline_on_you", subject="Renew passport",
                         deadline=utcnow_naive() + timedelta(days=10), evidence_quote="q",
                         status="pending"))

    result = handlers.enforce_retention({}, ctx())

    assert result["emails"] == 1
    with database.session_scope() as s:
        remaining = set(s.scalars(select(RawEmail.id)))
        assert remaining == {needed, recent}
        assert stale not in remaining
        [purged] = s.scalars(select(Event).where(Event.type == recorder.DATA_PURGED)).all()
        assert purged.payload["emails"] == 1


def test_upkeep_drops_metric_windows_the_system_page_no_longer_reads():
    """Metrics are the deployment's, not a user's, so the worker's upkeep prunes them."""
    from src.jobs import queue

    now = utcnow_naive()
    with database.session_scope() as s:
        for days_ago in (3, 1):
            end = now - timedelta(days=days_ago)
            s.add(MetricSnapshot(source="flink", window="1m", window_start=end - timedelta(minutes=1),
                                 window_end=end, metrics={}))

    with database.session_scope() as s:
        assert queue.prune_metric_snapshots(s) == 1
    with database.session_scope() as s:
        [kept] = s.scalars(select(MetricSnapshot)).all()
        assert kept.window_end > now - timedelta(days=2)


def test_not_keeping_bodies_clears_them_once_analysed_and_only_then():
    save_setting("privacy", {"retentionDays": 0, "keepEmailBodies": False})
    analysed = old_email("done", days_ago=1, processed=True)
    waiting = old_email("todo", days_ago=1, processed=False)

    handlers.enforce_retention({}, ctx())

    with database.session_scope() as s:
        assert s.get(RawEmail, analysed).body_text is None
        assert s.get(RawEmail, waiting).body_text == "body"   # the model still needs it


def test_the_default_settings_delete_nothing():
    old_email("ancient", days_ago=3650, processed=True)
    assert handlers.enforce_retention({}, ctx()) == {"emails": 0, "events": 0, "bodies": 0}
