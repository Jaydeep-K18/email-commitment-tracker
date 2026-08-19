"""Tests for Google sign-in and Google Calendar writing (Phase 9).

No network and no real credentials: the Calendar and Gmail clients are replaced
by fakes that record what they were asked to do. What is worth pinning down is
not that the Google libraries work, but that *we* call them correctly — that a
second sync patches instead of duplicating, that a deleted event is recreated
rather than failing forever, and that a Google outage cannot damage the local
`.ics` path that was working fine.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src import config, first_run
from src.auth import google_auth
from src.storage import database
from src.storage.models import Base, Commitment, RawEmail, SyncLog
from src.sync import google_calendar, sync_engine

NOW = datetime(2026, 8, 12, 9, 0)


# --- Fakes -----------------------------------------------------------------

class FakeHttpError(Exception):
    """Stands in for googleapiclient.errors.HttpError."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.resp = type("resp", (), {"status": status})()


class FakeEvents:
    """Records calls and behaves like the events() collection."""

    def __init__(self, owner: "FakeCalendar") -> None:
        self.owner = owner

    def insert(self, calendarId, body):  # noqa: N803 - Google's parameter name
        self.owner.inserted.append((calendarId, body))
        event_id = f"evt-{len(self.owner.store) + 1}"
        self.owner.store[event_id] = body
        return _Execute({"id": event_id})

    def patch(self, calendarId, eventId, body):  # noqa: N803
        if eventId in self.owner.missing:
            return _Execute(error=FakeHttpError(404))
        self.owner.patched.append((eventId, body))
        self.owner.store[eventId] = body
        return _Execute({"id": eventId})

    def delete(self, calendarId, eventId):  # noqa: N803
        if eventId in self.owner.missing:
            return _Execute(error=FakeHttpError(410))
        self.owner.deleted.append(eventId)
        self.owner.store.pop(eventId, None)
        return _Execute({})


class _Execute:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def execute(self):
        if self._error is not None:
            raise self._error
        return self._result


class FakeCalendar:
    """A stand-in Calendar service that remembers everything it was told."""

    def __init__(self, missing: set[str] | None = None) -> None:
        self.store: dict[str, dict] = {}
        self.inserted: list[tuple[str, dict]] = []
        self.patched: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        #: Event ids the server should claim no longer exist.
        self.missing = missing or set()

    def events(self):
        return FakeEvents(self)


@pytest.fixture(autouse=True)
def fake_http_error(monkeypatch):
    """Make the modules' lazily-imported HttpError our fake."""
    import googleapiclient.errors

    monkeypatch.setattr(googleapiclient.errors, "HttpError", FakeHttpError)
    yield


@pytest.fixture()
def session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'g.db'}", future=True)
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", Factory)
    db = Factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def make(session, **overrides) -> Commitment:
    email = RawEmail(message_id=f"<m{overrides.get('n', 1)}>", subject="src")
    session.add(email)
    session.flush()
    fields = dict(
        email_id=email.id,
        type="deadline_on_you",
        subject="Send the report",
        deadline=NOW + timedelta(days=2),
        counterparty_name="Alice Chen",
        counterparty_email="alice@x.com",
        evidence_quote="please send the report by Friday",
        confidence=0.9,
        vip_tier="CRITICAL",
        status="pending",
    )
    fields.pop("n", None)
    overrides.pop("n", None)
    fields.update(overrides)
    commitment = Commitment(**fields)
    session.add(commitment)
    session.flush()
    return commitment


# --- Event mapping ---------------------------------------------------------

def test_a_timed_deadline_becomes_a_one_hour_event(session):
    commitment = make(session, deadline=datetime(2026, 8, 20, 14, 30))
    body = google_calendar.event_body(commitment)

    assert body["start"]["dateTime"].startswith("2026-08-20T14:30")
    assert body["end"]["dateTime"].startswith("2026-08-20T15:30")


def test_a_timed_event_keeps_wall_clock_time_but_states_its_offset(session):
    """Two requirements that pull against each other, and both are real.

    Deadlines are the wall-clock time written in the email, so converting to UTC
    would show a 5pm deadline at 22:30 for a reader in IST. But Google rejects a
    naive dateTime outright ("Missing time zone definition for start time"),
    where iCalendar accepts it as floating local time — which is why the .ics
    feed worked while every timed event was refused by the API.

    Stating the local offset satisfies both: the digits stay 17:00 and the
    request is well-formed. An earlier version of this test asserted the absence
    of an offset and so encoded the bug.
    """
    commitment = make(session, deadline=datetime(2026, 8, 20, 17, 0))
    stamp = google_calendar.event_body(commitment)["start"]["dateTime"]

    assert stamp.startswith("2026-08-20T17:00:00")   # not shifted to UTC
    assert not stamp.endswith("Z")
    # Offset present, in either direction of UTC.
    assert ("+" in stamp) or (stamp.count("-") > 2)


def test_every_timed_event_is_acceptable_to_google(session):
    """Guards the exact 400 the API returned: a timed start or end with no
    offset and no timeZone field is rejected."""
    commitment = make(session, deadline=datetime(2026, 8, 20, 9, 15))
    body = google_calendar.event_body(commitment)

    for edge in ("start", "end"):
        stamp = body[edge]["dateTime"]
        has_offset = stamp.endswith("Z") or "+" in stamp or stamp.count("-") > 2
        assert has_offset or "timeZone" in body[edge], (
            f"{edge} would be refused: {body[edge]!r}"
        )


def test_an_all_day_event_needs_no_timezone(session):
    """A bare date is timezone-less by definition, and Google accepts it — the
    five all-day events were the only ones that survived the first real sync."""
    commitment = make(session, deadline=datetime(2026, 8, 20, 0, 0))
    body = google_calendar.event_body(commitment)

    assert body["start"] == {"date": "2026-08-20"}
    assert "dateTime" not in body["start"]


def test_a_midnight_deadline_becomes_an_all_day_event(session):
    commitment = make(session, deadline=datetime(2026, 8, 20, 0, 0))
    body = google_calendar.event_body(commitment)

    assert body["start"] == {"date": "2026-08-20"}
    # Google treats the all-day end as exclusive, so it is the following day.
    assert body["end"] == {"date": "2026-08-21"}


def test_the_event_repeats_what_the_ics_feed_says(session):
    """The two calendars must not describe the same commitment differently."""
    from src.sync import ics_builder

    commitment = make(session)
    body = google_calendar.event_body(commitment)

    assert body["summary"] == ics_builder.build_summary(commitment)
    assert body["description"] == ics_builder.build_description(commitment)


def test_the_event_carries_the_evidence_sentence(session):
    commitment = make(session, evidence_quote="please send the report by Friday")
    body = google_calendar.event_body(commitment)
    assert "please send the report by Friday" in body["description"]


def test_events_are_tagged_as_ours(session):
    """So a cleanup pass can tell them from events the user made by hand."""
    body = google_calendar.event_body(make(session))
    private = body["extendedProperties"]["private"]
    assert private[google_calendar.APP_TAG_KEY] == google_calendar.APP_TAG_VALUE


def test_reminder_lead_times_follow_config(session):
    timed = google_calendar.event_body(
        make(session, deadline=datetime(2026, 8, 20, 9, 0))
    )
    allday = google_calendar.event_body(
        make(session, n=2, deadline=datetime(2026, 8, 20, 0, 0))
    )

    assert timed["reminders"]["overrides"][0]["minutes"] == (
        config.CALENDAR_REMINDER_MINUTES
    )
    assert allday["reminders"]["overrides"][0]["minutes"] == (
        config.CALENDAR_ALLDAY_REMINDER_HOURS * 60
    )


def test_a_commitment_with_no_deadline_cannot_become_an_event(session):
    with pytest.raises(ValueError):
        google_calendar.event_body(make(session, deadline=None))


# --- Push behaviour --------------------------------------------------------

def test_a_new_commitment_creates_one_event(session):
    commitment = make(session)
    service = FakeCalendar()

    report = google_calendar.push(session, [commitment], service=service)

    assert report.created == 1
    assert report.updated == 0
    assert commitment.gcal_event_id == "evt-1"


def test_syncing_twice_updates_instead_of_duplicating(session):
    """The whole point of storing gcal_event_id."""
    commitment = make(session)
    service = FakeCalendar()

    google_calendar.push(session, [commitment], service=service)
    report = google_calendar.push(session, [commitment], service=service)

    assert report.created == 0
    assert report.updated == 1
    assert len(service.inserted) == 1        # not two events
    assert len(service.store) == 1


def test_an_event_deleted_by_hand_is_recreated(session):
    """Otherwise a user tidying their calendar breaks that commitment forever."""
    commitment = make(session)
    service = FakeCalendar()
    google_calendar.push(session, [commitment], service=service)

    service.missing.add(commitment.gcal_event_id)
    report = google_calendar.push(session, [commitment], service=service)

    assert report.created == 1
    assert commitment.gcal_event_id not in service.missing


def test_one_bad_commitment_does_not_stop_the_batch(session):
    good = make(session, n=1)
    broken = make(session, n=2, deadline=None)   # event_body will raise
    later = make(session, n=3, subject="Third")
    service = FakeCalendar()

    report = google_calendar.push(session, [good, broken, later], service=service)

    assert report.created == 2
    assert len(report.failed) == 1
    assert report.failed[0][0] == broken.id


def test_revoked_commitments_have_their_events_removed(session):
    commitment = make(session)
    service = FakeCalendar()
    google_calendar.push(session, [commitment], service=service)

    removed = google_calendar.remove(session, [commitment], service=service)

    assert removed == 1
    assert commitment.gcal_event_id is None
    assert service.store == {}


def test_removing_an_already_deleted_event_is_not_an_error(session):
    commitment = make(session)
    service = FakeCalendar()
    google_calendar.push(session, [commitment], service=service)
    service.missing.add(commitment.gcal_event_id)

    assert google_calendar.remove(session, [commitment], service=service) == 1
    assert commitment.gcal_event_id is None


def test_pushing_nothing_touches_no_service(session):
    # Passing None as the service proves no client was built.
    assert google_calendar.push(session, [], service=None).created == 0


# --- Integration with run_sync ---------------------------------------------

def test_run_sync_skips_google_when_not_signed_in(session, monkeypatch, tmp_path):
    monkeypatch.setattr(google_calendar, "is_available", lambda: False)
    make(session)

    report = sync_engine.run_sync(session, path=tmp_path / "cal.ics")

    assert report.ok
    assert report.google_connected is False
    assert report.google_created is None


def test_run_sync_pushes_to_google_when_signed_in(session, monkeypatch, tmp_path):
    service = FakeCalendar()
    monkeypatch.setattr(google_calendar, "is_available", lambda: True)
    monkeypatch.setattr(google_calendar, "build_service", lambda *a, **k: service)
    make(session)

    report = sync_engine.run_sync(session, path=tmp_path / "cal.ics")

    assert report.google_connected is True
    assert report.google_created == 1
    assert report.ok


def test_a_google_outage_does_not_fail_the_local_publish(session, monkeypatch, tmp_path):
    """The .ics file is the guaranteed output. Losing the network must not turn
    a successful publish into a failed cycle."""
    def explode(*_args, **_kwargs):
        raise RuntimeError("no route to host")

    monkeypatch.setattr(google_calendar, "is_available", lambda: True)
    monkeypatch.setattr(google_calendar, "build_service", explode)
    make(session)

    target = tmp_path / "cal.ics"
    report = sync_engine.run_sync(session, path=target)

    assert report.ok                     # the cycle still succeeded
    assert report.bytes_written > 0      # and the feed was written
    assert target.exists()
    assert report.google_errors          # while still reporting the problem


def test_google_failures_stay_out_of_the_ics_retry_tally(session, tmp_path):
    """A spell offline must not burn through SYNC_MAX_RETRIES and make the app
    give up publishing a commitment that the local feed handled fine."""
    commitment = make(session)
    for _ in range(config.SYNC_MAX_RETRIES + 2):
        database.log_sync(
            session, commitment.id, action="google_synced",
            status="failed", error_message="offline",
        )

    assert database.failed_sync_attempts(session) == {}
    assert database.sync_retry_queue(session) == []


def test_ics_failures_are_still_counted(session):
    """The guard above must not have disabled the retry queue entirely."""
    commitment = make(session)
    database.log_sync(
        session, commitment.id, action="created", status="failed",
        error_message="disk full",
    )
    assert database.failed_sync_attempts(session) == {commitment.id: 1}


# --- Token storage ---------------------------------------------------------

class FakeCreds:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_json(self) -> str:
        return json.dumps(self._payload)


@pytest.fixture()
def memory_keyring(monkeypatch):
    """An in-memory keyring, so tests never touch the real credential store."""
    store: dict[tuple[str, str], str] = {}

    monkeypatch.setattr(
        google_auth.keyring, "set_password",
        lambda service, account, value: store.__setitem__((service, account), value),
    )
    monkeypatch.setattr(
        google_auth.keyring, "get_password",
        lambda service, account: store.get((service, account)),
    )
    monkeypatch.setattr(
        google_auth.keyring, "delete_password",
        lambda service, account: store.pop((service, account), None),
    )
    return store


def test_a_token_round_trips_through_the_keyring(memory_keyring):
    google_auth.store_token(
        FakeCreds({"refresh_token": "r", "scopes": list(config.GOOGLE_MAIL_SCOPES)}),
        email="me@example.com",
    )

    assert google_auth.is_signed_in()
    account = google_auth.account()
    assert account.email == "me@example.com"
    assert account.has_mail and account.has_calendar


def test_a_refresh_does_not_lose_the_recorded_address(memory_keyring):
    """store_token is called again on every refresh, with credentials that carry
    no address — the one captured at sign-in must survive."""
    google_auth.store_token(FakeCreds({"refresh_token": "r"}), email="me@example.com")
    google_auth.store_token(FakeCreds({"refresh_token": "r2"}))   # a refresh

    assert google_auth.account().email == "me@example.com"


def test_disconnecting_forgets_the_token(memory_keyring):
    google_auth.store_token(FakeCreds({"refresh_token": "r"}), email="me@x.com")
    google_auth.clear_token()

    assert not google_auth.is_signed_in()
    assert google_auth.account() is None


def test_a_corrupted_token_reads_as_not_signed_in(memory_keyring):
    """A bad keyring entry must not wedge the app into an unusable state."""
    memory_keyring[(config.KEYRING_SERVICE, google_auth.KEYRING_ACCOUNT)] = "{not json"

    assert google_auth.stored_token() is None
    assert not google_auth.is_signed_in()


def test_asking_for_credentials_while_signed_out_says_so(memory_keyring):
    with pytest.raises(google_auth.GoogleAuthError, match="Not signed in"):
        google_auth.credentials()


# --- Setup state -----------------------------------------------------------

@pytest.fixture()
def local_model_ready(monkeypatch):
    """Pretend Ollama is installed and the model pulled.

    ``setup_state()`` asks Ollama directly, so without this the result depends on
    whatever happens to be running on the machine executing the tests — which is
    how these tests used to pass on a developer laptop and would have failed on a
    clean one.
    """
    from src.extraction.ollama_client import OllamaClient, OllamaHealth

    monkeypatch.setattr(
        OllamaClient,
        "health",
        lambda self: OllamaHealth(running=True, model_present=True, host="stub"),
    )


def stub_account(monkeypatch, *, scopes=()):
    monkeypatch.setattr(
        google_auth,
        "account",
        lambda: google_auth.GoogleAccount(email="me@example.com", scopes=scopes),
    )


def test_google_with_mail_access_completes_setup(monkeypatch, local_model_ready):
    stub_account(monkeypatch, scopes=config.GOOGLE_MAIL_SCOPES)
    monkeypatch.setattr(first_run, "password_is_stored", lambda *_a: False)
    monkeypatch.setattr(config, "IMAP_USER", "")

    assert first_run.setup_state().complete


def test_signing_in_without_the_mail_scope_does_not_complete_setup(
    monkeypatch, local_model_ready
):
    """Sign-in now asks for identity only, and identity grants no mailbox.

    Treating any sign-in as sufficient would let the app declare itself ready and
    then fetch nothing at all.
    """
    stub_account(monkeypatch, scopes=config.GOOGLE_IDENTITY_SCOPES)
    monkeypatch.setattr(first_run, "password_is_stored", lambda *_a: False)
    monkeypatch.setattr(config, "IMAP_USER", "")

    state = first_run.setup_state()
    assert state.signed_in_with_google is True
    assert state.complete is False
    assert "address" in state.missing


def test_an_app_password_alone_still_completes_setup(monkeypatch, local_model_ready):
    """Non-Gmail mailboxes have no other route, so this must keep working."""
    monkeypatch.setattr(google_auth, "account", lambda: None)
    monkeypatch.setattr(first_run, "password_is_stored", lambda *_a: True)
    monkeypatch.setattr(config, "IMAP_USER", "someone@fastmail.com")

    assert first_run.setup_state().complete


def test_neither_credential_means_setup_is_needed(monkeypatch, local_model_ready):
    monkeypatch.setattr(google_auth, "account", lambda: None)
    monkeypatch.setattr(first_run, "password_is_stored", lambda *_a: False)
    monkeypatch.setattr(config, "IMAP_USER", "")

    state = first_run.setup_state()
    assert not state.complete
    assert "address" in state.missing


def test_a_missing_local_model_blocks_setup(monkeypatch):
    """The silent failure this guards against: credentials all present, so the
    app reports itself ready, and then extraction dies with nothing on screen
    explaining that no model was ever installed."""
    from src.extraction.ollama_client import OllamaClient, OllamaHealth

    monkeypatch.setattr(
        OllamaClient,
        "health",
        lambda self: OllamaHealth(running=True, model_present=False, host="stub"),
    )
    monkeypatch.setattr(google_auth, "account", lambda: None)
    monkeypatch.setattr(first_run, "password_is_stored", lambda *_a: True)
    monkeypatch.setattr(config, "IMAP_USER", "someone@fastmail.com")

    state = first_run.setup_state()
    assert state.mailbox_ready is True      # credentials are fine
    assert state.complete is False          # ...but it still cannot work
    assert "model" in state.missing


# --- Gmail fetch -----------------------------------------------------------

class FakeMessages:
    def __init__(self, owner):
        self.owner = owner

    def list(self, userId, q, maxResults):  # noqa: N803
        self.owner.queries.append(q)
        newest_first = list(reversed(self.owner.raws))
        return _Execute(
            {"messages": [{"id": str(i)} for i, _ in enumerate(newest_first)]}
        )

    def get(self, userId, id, format):  # noqa: A002, N803
        newest_first = list(reversed(self.owner.raws))
        raw = newest_first[int(id)]
        return _Execute({"raw": base64.urlsafe_b64encode(raw).decode()})


class FakeGmail:
    def __init__(self, raws: list[bytes]) -> None:
        self.raws = raws
        self.queries: list[str] = []

    def users(self):
        return type("users", (), {"messages": lambda _self: FakeMessages(self)})()


def test_gmail_returns_raw_rfc822_oldest_first():
    """Matching the IMAP path's ordering keeps a capped batch identical
    whichever transport supplied it."""
    from src.collection import gmail_fetcher

    raws = [b"From: a@x.com\r\n\r\nfirst", b"From: b@x.com\r\n\r\nsecond"]
    service = FakeGmail(raws)

    assert gmail_fetcher.fetch_recent(service=service) == raws


def test_gmail_decodes_url_safe_base64():
    """Gmail uses the URL-safe alphabet; standard base64 corrupts the message."""
    from src.collection import gmail_fetcher

    body = b"From: a@x.com\r\n\r\n" + bytes(range(250, 256)) * 4
    service = FakeGmail([body])

    assert gmail_fetcher.fetch_recent(service=service)[0] == body


def test_the_gmail_search_window_matches_the_imap_one():
    from src.collection import gmail_fetcher

    query = gmail_fetcher.search_query(lookback_days=7)
    assert query.startswith("after:")


def test_the_fetcher_falls_back_to_imap_when_google_fails(monkeypatch):
    """A revoked token must not strand a user who still has an app password."""
    from src.collection import email_fetcher, gmail_fetcher

    monkeypatch.setattr(gmail_fetcher, "is_available", lambda: True)
    monkeypatch.setattr(
        gmail_fetcher, "fetch_recent",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("token revoked")),
    )
    used_imap = []
    monkeypatch.setattr(
        email_fetcher, "connect",
        lambda: used_imap.append(True) or type("i", (), {"logout": lambda _s: None})(),
    )
    monkeypatch.setattr(email_fetcher, "fetch_recent", lambda _imap: [b"raw"])

    assert email_fetcher.collect_raw_messages() == [b"raw"]
    assert used_imap == [True]
