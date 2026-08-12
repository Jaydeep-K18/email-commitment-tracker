"""Tests for the local API the Gmail side panel uses (Phase 10).

The security boundary is the main event here. The server listens on loopback,
which is *not* a boundary: any page the user has open can issue a fetch at
127.0.0.1. So every ``/api/*`` route must refuse an absent or wrong token, while
``/calendar.ics`` must stay open — a calendar app subscribing to a feed cannot
send a custom header.

The local model is never called: extraction is stubbed, because what is under
test is the wiring, not the model.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src import config
from src.extraction.schemas import ExtractedCommitment
from src.server import api, api_token
from src.server.calendar_server import app
from src.storage import database
from src.storage.models import Base, Commitment, RawEmail

NOW = datetime(2026, 8, 12, 10, 0)

#: Long enough to clear MessagePayload.is_readable's floor.
REAL_BODY = "Hi Jaydeep, please send the quarterly report by Friday 5pm. Thanks."


@pytest.fixture()
def token(tmp_path, monkeypatch):
    """A throwaway token file, so tests never read or write the real one."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return api_token.get_or_create_token()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}", future=True)
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", Factory)
    try:
        yield Factory
    finally:
        engine.dispose()


@pytest.fixture()
def client(db, token):
    return TestClient(app)


def auth(token):
    return {api_token.TOKEN_HEADER: token}


def message(**overrides) -> dict:
    payload = {
        "subject": "Quarterly report",
        "sender_name": "Alice Chen",
        "sender_email": "alice@example.com",
        "body": REAL_BODY,
        "thread_id": "thread-1",
    }
    payload.update(overrides)
    return payload


# --- The security boundary -------------------------------------------------

PROTECTED = [
    ("get", "/api/status", None),
    ("post", "/api/analyze", {"subject": "x", "body": REAL_BODY}),
    ("get", "/api/lookup?subject=x", None),
    ("post", "/api/commitments", {"subject": "x", "message": {}}),
    ("post", "/api/sync", None),
]


def call(client, method, path, body, headers=None):
    """httpx's get() rejects a json kwarg, so only POSTs carry a body."""
    if method == "get":
        return client.get(path, headers=headers)
    return client.post(path, json=body, headers=headers)


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_every_api_route_refuses_a_missing_token(client, method, path, body):
    assert call(client, method, path, body).status_code == 401


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_every_api_route_refuses_a_wrong_token(client, method, path, body):
    response = call(
        client, method, path, body,
        headers={api_token.TOKEN_HEADER: "not-the-token"},
    )
    assert response.status_code == 401


def test_the_calendar_feed_stays_open(client):
    """A calendar app cannot send a custom header, so the feed must not need one."""
    assert client.get("/calendar.ics").status_code == 200
    assert client.get("/health").status_code == 200


def test_the_rejection_says_how_to_fix_it(client):
    detail = client.get("/api/status").json()["detail"]
    assert "token" in detail.lower()
    assert "options" in detail.lower()


def test_a_token_is_not_guessable_by_length_alone(token):
    assert len(token) >= 24


def test_rotating_invalidates_the_previous_token(client, token):
    assert client.get("/api/status", headers=auth(token)).status_code == 200

    replacement = api_token.rotate_token()

    assert client.get("/api/status", headers=auth(token)).status_code == 401
    assert client.get("/api/status", headers=auth(replacement)).status_code == 200


def test_an_empty_token_never_authorises(token, monkeypatch):
    monkeypatch.setattr(api_token, "read_token", lambda: "")
    assert api_token.token_matches("") is False
    assert api_token.token_matches("anything") is False


# --- CORS ------------------------------------------------------------------

def test_an_extension_origin_is_allowed(client, token):
    response = client.get(
        "/api/status",
        headers={**auth(token), "Origin": "chrome-extension://abcdefghijklmnop"},
    )
    assert response.headers.get("access-control-allow-origin") == (
        "chrome-extension://abcdefghijklmnop"
    )


def test_an_arbitrary_website_is_not_granted_cors(client, token):
    """The token is the real guard, but there is no reason to also hand a
    random site the browser's blessing."""
    response = client.get(
        "/api/status", headers={**auth(token), "Origin": "https://evil.example"}
    )
    assert "access-control-allow-origin" not in response.headers


# --- Status ----------------------------------------------------------------

def test_status_reports_what_the_app_can_do(client, token):
    body = client.get("/api/status", headers=auth(token)).json()
    assert body["ok"] is True
    assert "google_connected" in body
    assert body["calendar_url"].endswith("/calendar.ics")


# --- Analyze ---------------------------------------------------------------

@pytest.fixture()
def fake_extraction(monkeypatch):
    """Replace the local model with something deterministic."""
    import src.extraction.pipeline as pipeline
    from src.extraction.pipeline import ExtractionOutcome

    calls = []

    def fake(client, email, user_email=None):
        calls.append(email)
        return ExtractionOutcome(
            email_id=email.id,
            commitments=[
                ExtractedCommitment(
                    type="deadline_on_you",
                    subject="Send the quarterly report",
                    deadline=NOW + timedelta(days=3),
                    counterparty="Alice Chen",
                    evidence_quote="please send the quarterly report by Friday 5pm",
                    confidence=0.92,
                )
            ],
        )

    monkeypatch.setattr(pipeline, "extract_from_email", fake)
    monkeypatch.setattr(api, "_ollama_available", lambda: True, raising=False)
    monkeypatch.setattr(
        "src.extraction.ollama_client.OllamaClient", lambda *a, **k: object()
    )
    return calls


def test_analyze_returns_what_the_model_found(client, token, fake_extraction):
    response = client.post("/api/analyze", json=message(), headers=auth(token))

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert len(body["commitments"]) == 1
    assert body["commitments"][0]["subject"] == "Send the quarterly report"


def test_analyze_stores_nothing(client, token, db, fake_extraction):
    """The panel previews; only pressing the button commits."""
    client.post("/api/analyze", json=message(), headers=auth(token))

    with database.session_scope() as session:
        assert session.query(Commitment).count() == 0
        assert session.query(RawEmail).count() == 0


def test_analyze_reuses_the_real_pipeline(client, token, fake_extraction):
    """Passing a RawEmail built the same way the scheduler does is what stops
    the panel and the background pipeline disagreeing about an email."""
    client.post("/api/analyze", json=message(), headers=auth(token))

    (email,) = fake_extraction
    assert isinstance(email, RawEmail)
    assert email.subject == "Quarterly report"
    assert email.body_text == REAL_BODY
    assert email.sender_email == "alice@example.com"


def test_an_unreadable_page_says_so_rather_than_finding_nothing(client, token):
    """Gmail's DOM changes without notice. An empty body reported as 'no
    commitments' looks identical to a genuinely uneventful email."""
    response = client.post(
        "/api/analyze", json=message(body=""), headers=auth(token)
    )

    body = response.json()
    assert body["readable"] is False
    assert "could not read" in body["message"].lower()
    assert body["commitments"] == []


def test_a_stub_of_a_body_is_treated_as_unreadable(client, token):
    body = client.post(
        "/api/analyze", json=message(body="Hi"), headers=auth(token)
    ).json()
    assert body["readable"] is False


def test_a_model_that_is_not_running_is_reported_plainly(client, token, monkeypatch):
    import src.extraction.pipeline as pipeline

    def explode(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(pipeline, "extract_from_email", explode)
    monkeypatch.setattr(
        "src.extraction.ollama_client.OllamaClient", lambda *a, **k: object()
    )

    body = client.post("/api/analyze", json=message(), headers=auth(token)).json()

    assert body["ok"] is False
    assert "ollama" in body["message"].lower()


def test_discarded_records_are_explained(client, token, monkeypatch):
    """'Found nothing' and 'found things whose quote was invented' are very
    different signals and must not read the same."""
    import src.extraction.pipeline as pipeline
    from src.extraction.pipeline import ExtractionOutcome

    monkeypatch.setattr(
        pipeline, "extract_from_email",
        lambda *a, **k: ExtractionOutcome(email_id=None, fabricated_evidence=2),
    )
    monkeypatch.setattr(
        "src.extraction.ollama_client.OllamaClient", lambda *a, **k: object()
    )

    body = client.post("/api/analyze", json=message(), headers=auth(token)).json()

    assert body["commitments"] == []
    assert "discarded" in body["message"].lower()


# --- Adding ----------------------------------------------------------------

def add_payload(**overrides) -> dict:
    payload = {
        "type": "deadline_on_you",
        "subject": "Send the quarterly report",
        "deadline": (NOW + timedelta(days=3)).isoformat(),
        "counterparty": "Alice Chen",
        "evidence_quote": "please send the quarterly report by Friday 5pm",
        "confidence": 0.92,
        "message": message(),
    }
    payload.update(overrides)
    return payload


def test_adding_stores_a_commitment_ready_for_the_calendar(client, token, db):
    response = client.post(
        "/api/commitments", json=add_payload(), headers=auth(token)
    )

    assert response.status_code == 200
    with database.session_scope() as session:
        (commitment,) = session.query(Commitment).all()
        assert commitment.subject == "Send the quarterly report"
        # The user pressed a button; that is the approval.
        assert commitment.sync_approved is True


def test_an_added_commitment_reaches_the_feed(client, token, db, tmp_path):
    """The whole point: pressing the button puts it on the calendar."""
    from src.sync import sync_engine

    client.post("/api/commitments", json=add_payload(), headers=auth(token))

    with database.session_scope() as session:
        published = sync_engine.calendar_commitments(session)

    assert [c.subject for c in published] == ["Send the quarterly report"]


def test_adding_from_the_panel_beats_the_skip_default(client, token, db):
    """Unknown senders are tiered SKIP automatically, so without an override
    "Add to calendar" would silently do nothing for anyone not already a VIP —
    which is most people the user emails with.
    """
    from src.sync import sync_engine

    client.post("/api/commitments", json=add_payload(), headers=auth(token))

    with database.session_scope() as session:
        (commitment,) = session.query(Commitment).all()
        assert commitment.vip_tier == "SKIP"      # sender genuinely is not a VIP
        assert commitment.manually_added is True
        decision = sync_engine.decide(commitment)

    assert decision.should_sync is True


def test_approving_a_skip_commitment_is_still_not_a_way_in(db):
    """The Phase 5 invariant, unchanged: the review queue must not become a
    route past the skip list. Only hand-picking an open email is."""
    from src.sync import sync_engine

    commitment = Commitment(
        email_id=None,
        type="deadline_on_you",
        subject="Newsletter deadline",
        deadline=NOW + timedelta(days=1),
        evidence_quote="q",
        confidence=0.9,
        vip_tier="SKIP",
        status="pending",
        sync_approved=True,
        manually_added=False,
    )

    decision = sync_engine.decide(commitment)
    assert decision.should_sync is False
    assert "skip list" in decision.reason


def test_a_dismissed_commitment_stays_off_even_if_added_by_hand(db):
    """Status still outranks everything: un-dismissing is the way back."""
    from src.sync import sync_engine

    commitment = Commitment(
        email_id=None,
        type="deadline_on_you",
        subject="Cancelled thing",
        deadline=NOW + timedelta(days=1),
        evidence_quote="q",
        confidence=0.9,
        vip_tier="SKIP",
        status="dismissed",
        sync_approved=True,
        manually_added=True,
    )

    assert sync_engine.decide(commitment).should_sync is False


def test_the_source_email_is_marked_processed(client, token, db):
    """Otherwise the background pipeline would run the model over it again."""
    client.post("/api/commitments", json=add_payload(), headers=auth(token))

    with database.session_scope() as session:
        (email,) = session.query(RawEmail).all()
        assert email.processed is True


def test_adding_twice_from_one_thread_reuses_the_email_row(client, token, db):
    """save_email returns None for a duplicate Message-ID; without handling it
    the second add would have no email to attach to."""
    client.post("/api/commitments", json=add_payload(), headers=auth(token))
    second = client.post(
        "/api/commitments",
        json=add_payload(subject="Also book the room"),
        headers=auth(token),
    )

    assert second.status_code == 200
    with database.session_scope() as session:
        assert session.query(RawEmail).count() == 1
        assert session.query(Commitment).count() == 2


def test_a_commitment_with_no_subject_is_refused(client, token, db):
    response = client.post(
        "/api/commitments", json=add_payload(subject=""), headers=auth(token)
    )
    assert response.status_code == 422


def test_an_added_commitment_picks_up_its_vip_tier(client, token, db):
    with database.session_scope() as session:
        database.add_vip_contact(
            session, "alice@example.com", match_type="exact_email", tier="CRITICAL"
        )

    client.post("/api/commitments", json=add_payload(), headers=auth(token))

    with database.session_scope() as session:
        (commitment,) = session.query(Commitment).all()
        assert commitment.vip_tier == "CRITICAL"


# --- Lookup ----------------------------------------------------------------

def test_lookup_reports_an_unknown_message(client, token, db):
    body = client.get(
        "/api/lookup?subject=something+nobody+tracked", headers=auth(token)
    ).json()
    assert body["known"] is False


def test_lookup_recognises_a_message_already_captured(client, token, db):
    client.post("/api/commitments", json=add_payload(), headers=auth(token))

    body = client.get(
        "/api/lookup?subject=Send+the+quarterly+report", headers=auth(token)
    ).json()

    assert body["known"] is True
    assert body["subject"] == "Send the quarterly report"


# --- Sync ------------------------------------------------------------------

def test_sync_publishes_immediately(client, token, db, tmp_path, monkeypatch):
    """Without this the user would press Add and watch nothing happen for up to
    half an hour."""
    monkeypatch.setattr(config, "ICS_PATH", tmp_path / "calendar.ics")
    client.post("/api/commitments", json=add_payload(), headers=auth(token))

    body = client.post("/api/sync", headers=auth(token)).json()

    assert body["ok"] is True
    assert body["published"] == 1
