"""v2 phase 2: the event log, the smart-inbox signals and the classifier.

The events are what the activity timeline, the live feed, notifications and the
Flink metrics are all built from, so the tests below check the *journey*: that
every step of one email's processing lands under the same correlation id, in
the same transaction as the change it describes.
"""
from __future__ import annotations

import json
from datetime import datetime
from email.message import EmailMessage

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.classification import classifier as c
from src.classification.classifier import EmailSignals, classify
from src.classification.service import apply_classification, classify_unclassified
from src.collection import sent_fetcher
from src.collection.email_parser import ParsedEmail, parse_email_message
from src.events import recorder
from src.extraction.pipeline import analyze_and_store, record_extraction_failure
from src.storage import database
from src.storage.models import Base, Commitment, Event, RawEmail, SentMessage
from src.sync import google_calendar, sync_engine


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """A real database wired into the storage layer, so session_scope() works."""
    engine = create_engine(f"sqlite:///{tmp_path / 'events.db'}", future=True)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(
        database, "SessionLocal", sessionmaker(bind=engine, future=True, expire_on_commit=False)
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def session(engine):
    with database.SessionLocal() as s:
        yield s


def events(session, **filters) -> list[Event]:
    query = select(Event).order_by(Event.id)
    for name, value in filters.items():
        query = query.where(getattr(Event, name) == value)
    return list(session.scalars(query))


# --- Parser: the signals the classifier reads -----------------------------

def message(**headers) -> EmailMessage:
    msg = EmailMessage()
    msg["Message-ID"] = "<m1@example.com>"
    msg["From"] = "Priya Nair <priya@example.com>"
    msg["To"] = "me@example.com"
    msg["Subject"] = "Hello"
    for name, value in headers.items():
        msg[name.replace("_", "-")] = value
    msg.set_content("Body text.")
    return msg


@pytest.mark.parametrize(
    "headers",
    [
        {"List_Unsubscribe": "<mailto:unsub@example.com>"},
        {"List_Id": "news.example.com"},
        {"Precedence": "bulk"},
        {"Auto_Submitted": "auto-generated"},
    ],
)
def test_mailing_list_and_machine_headers_mark_mail_as_bulk(headers):
    assert parse_email_message(bytes(message(**headers))).is_bulk is True


def test_a_plain_email_and_an_explicit_auto_submitted_no_are_not_bulk():
    assert parse_email_message(bytes(message())).is_bulk is False
    assert parse_email_message(bytes(message(Auto_Submitted="no"))).is_bulk is False


def test_a_calendar_part_is_an_invite_and_an_attachment_is_an_attachment():
    msg = message()
    msg.add_attachment(
        b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", maintype="text", subtype="calendar",
        filename="invite.ics",
    )
    parsed = parse_email_message(bytes(msg))
    assert parsed.has_invite is True
    assert parsed.has_attachments is True


def test_reply_and_cc_headers_are_kept():
    parsed = parse_email_message(bytes(message(
        In_Reply_To="<original@example.com>", Cc="A <a@x.com>, b@y.com"
    )))
    assert parsed.in_reply_to == "original@example.com"   # stored without brackets
    assert parsed.cc == "a@x.com, b@y.com"


# --- Classifier rules -------------------------------------------------------

def signals(**fields) -> EmailSignals:
    defaults = dict(subject="Hello", body_text="", sender_email="someone@acme.com")
    defaults.update(fields)
    return EmailSignals(**defaults)


def test_a_calendar_invite_is_a_meeting_whoever_sent_it():
    result = classify(signals(has_invite=True, is_bulk=True, sender_email="noreply@x.com"))
    assert result.category == c.MEETING


def test_something_to_do_outranks_a_meeting_in_the_same_email():
    result = classify(signals(commitment_types=("meeting", "deadline_on_you")))
    assert result.category == c.ACTION_REQUIRED
    assert result.source == c.SOURCE_LLM


def test_a_request_from_a_person_needs_action():
    result = classify(signals(
        sender_email="priya@gmail.com", body_text="Could you please send the slides?"
    ))
    assert result.category == c.ACTION_REQUIRED
    assert "please send" in result.reason


def test_the_same_request_in_a_newsletter_is_not_action_required():
    """'Please confirm your subscription' is asking for a click, not attention."""
    result = classify(signals(
        is_bulk=True, body_text="Please confirm your subscription. Unsubscribe here."
    ))
    assert result.category != c.ACTION_REQUIRED


def test_a_critical_contact_is_important():
    assert classify(signals(vip_tier="CRITICAL")).category == c.IMPORTANT


def test_marketing_is_low_priority_and_notifications_are_updates():
    promo = classify(signals(is_bulk=True, subject="50% off this weekend only"))
    notice = classify(signals(sender_email="no-reply@accounts.google.com",
                              subject="Security alert"))
    assert promo.category == c.LOW_PRIORITY
    assert notice.category == c.UPDATE


def test_mail_stored_before_headers_were_kept_is_caught_by_its_unsubscribe_line():
    result = classify(signals(subject="Weekly digest", body_text="... Unsubscribe | Preferences"))
    assert result.category == c.LOW_PRIORITY


@pytest.mark.parametrize(
    "sender,expected",
    [
        ("priya@gmail.com", c.IMPORTANT),           # a person's own mailbox
        ("student_b23@it.vjti.ac.in", c.IMPORTANT), # a university address
        ("partnerships@bigcorp.com", c.UPDATE),     # an organisation writing in
    ],
)
def test_unranked_senders_are_judged_by_whose_address_it_is(sender, expected):
    assert classify(signals(sender_email=sender)).category == expected


def test_gmail_is_not_mistaken_for_a_bulk_mail_subdomain():
    assert signals(sender_email="priya@gmail.com").automated is False
    assert signals(sender_email="news@mail.bigcorp.com").automated is True


def test_a_reply_in_a_thread_is_important_even_from_an_organisation():
    assert classify(signals(sender_email="bob@bigcorp.com", is_reply=True)).category == c.IMPORTANT


def test_classification_is_deterministic():
    s = signals(body_text="RSVP by Friday", sender_email="a@gmail.com")
    assert classify(s) == classify(s)


# --- Applying categories ----------------------------------------------------

def stored_email(session, **fields) -> RawEmail:
    defaults = dict(message_id=f"m-{len(fields)}-{id(fields)}", subject="Hello",
                    sender_email="priya@gmail.com", body_text="")
    defaults.update(fields)
    email = RawEmail(**defaults)
    session.add(email)
    session.flush()
    return email


def test_a_category_the_user_chose_is_never_overwritten(session):
    email = stored_email(session, category="low_priority", category_source=c.SOURCE_USER)
    changed = apply_classification(session, email, commitment_types=("deadline_on_you",))
    assert changed is False
    assert email.category == "low_priority"
    assert events(session, type=recorder.EMAIL_CLASSIFIED) == []


def test_reclassifying_without_a_change_records_nothing(session):
    email = stored_email(session)
    assert apply_classification(session, email, commitment_types=()) is True
    assert apply_classification(session, email, commitment_types=()) is False
    assert len(events(session, type=recorder.EMAIL_CLASSIFIED)) == 1


def test_a_change_of_category_records_where_it_moved_from(session):
    email = stored_email(session)
    apply_classification(session, email, commitment_types=())
    apply_classification(session, email, commitment_types=("deadline_on_you",))

    moved = events(session, type=recorder.EMAIL_CLASSIFIED)[-1]
    assert moved.payload["previous"] == c.IMPORTANT
    assert moved.payload["category"] == c.ACTION_REQUIRED
    assert moved.message.startswith("Moved from Important to Action Required")


def test_migrated_emails_without_a_category_are_backfilled(session):
    for i in range(3):
        stored_email(session, message_id=f"old-{i}")
    assert classify_unclassified(session) == 3
    assert session.scalars(select(RawEmail).where(RawEmail.category.is_(None))).all() == []


# --- Events -----------------------------------------------------------------

def test_an_unknown_event_type_is_refused_rather_than_stored(session):
    with pytest.raises(ValueError, match="unknown event type"):
        recorder.record_event(session, "email.recieved", "typo")
    with pytest.raises(ValueError, match="unknown severity"):
        recorder.record_event(session, recorder.EMAIL_RECEIVED, "x", severity="fatal")


def test_storing_an_email_records_its_arrival_in_the_same_transaction(session):
    parsed = ParsedEmail(
        message_id="arrival@x", thread_id=None, sender_name="Priya Nair",
        sender_email="priya@gmail.com", recipient_email="me@x.com",
        subject="Report", body_text="hi", received_at=datetime(2026, 9, 1),
        is_bulk=True,
    )
    email = database.save_email(session, parsed, vip_tier="CRITICAL")

    [received] = events(session, type=recorder.EMAIL_RECEIVED)
    assert received.correlation_id == f"email:{email.id}"
    assert received.message == "Email from Priya Nair: Report"
    assert email.is_bulk is True

    session.rollback()   # the email and its event leave together
    assert events(session) == []


class FakeModel:
    def __init__(self, *responses):
        self.responses = list(responses)

    def generate(self, prompt, system=None, schema=None, **kwargs):
        return self.responses.pop(0) if self.responses else '{"commitments": []}'


BODY = "Hi,\n\nPlease submit your final project report by 15th August.\n"


def model_reply() -> str:
    return json.dumps({"commitments": [{
        "type": "deadline_on_you",
        "subject": "Submit final project report",
        "deadline": "2026-08-15",
        "counterparty": "Dr. Alice Chen",
        "direction": "outgoing",
        "evidence_quote": "Please submit your final project report by 15th August",
        "confidence": 0.95,
    }]})


def test_one_emails_whole_journey_shares_a_correlation_id(session):
    email = stored_email(
        session, message_id="journey@x", body_text=BODY, vip_tier="CRITICAL",
        sender_email="alice@university.edu", received_at=datetime(2026, 8, 1, 9),
    )
    apply_classification(session, email, commitment_types=())

    analyze_and_store(session, FakeModel(model_reply()), email)

    journey = [e.type for e in events(session, correlation_id=f"email:{email.id}")]
    assert journey == [
        recorder.EMAIL_CLASSIFIED,     # by rules, on arrival
        recorder.COMMITMENT_CREATED,
        recorder.EMAIL_ANALYZED,
        recorder.EMAIL_CLASSIFIED,     # refined by what the model found
    ]
    assert email.processed is True
    assert email.category == c.ACTION_REQUIRED


def test_analysis_records_its_latency_for_the_metrics(session):
    email = stored_email(session, message_id="latency@x", body_text=BODY, vip_tier="CRITICAL")
    analyze_and_store(session, FakeModel(model_reply()), email)

    [analyzed] = events(session, type=recorder.EMAIL_ANALYZED)
    assert analyzed.payload["commitments"] == 1
    assert isinstance(analyzed.payload["duration_ms"], int)


def test_a_failed_analysis_is_recorded_even_though_its_work_is_rolled_back(engine):
    record_extraction_failure(42, TimeoutError("model took too long"))

    with database.SessionLocal() as s:
        [failed] = events(s, type=recorder.EXTRACTION_FAILED)
    assert failed.correlation_id == "email:42"
    assert failed.severity == "error"
    assert failed.payload["error_type"] == "TimeoutError"


# --- Calendar events ---------------------------------------------------------

def dated_commitment(session, **fields) -> Commitment:
    email = stored_email(session, message_id=f"cal-{id(fields)}")
    defaults = dict(
        email_id=email.id, type="deadline_on_you", subject="Send report",
        deadline=datetime(2026, 9, 10, 17, 0), evidence_quote="send it",
        confidence=0.9, vip_tier="CRITICAL",
    )
    defaults.update(fields)
    commitment = Commitment(**defaults)
    session.add(commitment)
    session.flush()
    return commitment


@pytest.fixture()
def no_google(monkeypatch):
    monkeypatch.setattr(google_calendar, "is_available", lambda: False)


def test_a_first_publish_is_recorded_on_the_source_emails_timeline(session, tmp_path, no_google):
    commitment = dated_commitment(session)

    sync_engine.run_sync(session, path=tmp_path / "c.ics")

    [created] = events(session, type=recorder.CALENDAR_EVENT_CREATED)
    assert created.correlation_id == f"email:{commitment.email_id}"
    assert created.payload["target"] == "calendar_file"


def test_republishing_does_not_announce_the_same_event_again(session, tmp_path, no_google):
    dated_commitment(session)
    sync_engine.run_sync(session, path=tmp_path / "c.ics")
    sync_engine.run_sync(session, path=tmp_path / "c.ics")

    assert len(events(session, type=recorder.CALENDAR_EVENT_CREATED)) == 1
    assert len(events(session, type=recorder.CALENDAR_SYNCED)) == 2


def test_a_dismissed_commitment_records_its_removal(session, tmp_path, no_google):
    commitment = dated_commitment(session)
    sync_engine.run_sync(session, path=tmp_path / "c.ics")
    commitment.status = "dismissed"
    sync_engine.run_sync(session, path=tmp_path / "c.ics")

    [removed] = events(session, type=recorder.CALENDAR_EVENT_REMOVED)
    assert "dismissed" in removed.message


class RejectingEvents:
    """A Calendar API whose inserts all fail, as with a missing time zone."""

    def insert(self, calendarId, body):  # noqa: N803
        raise RuntimeError("Missing time zone definition for start time")

    def patch(self, calendarId, eventId, body):  # noqa: N803
        raise RuntimeError("unused")


class RejectingService:
    def events(self):
        return RejectingEvents()


def test_a_google_rejection_is_recorded_as_a_failed_calendar_event(session, tmp_path, monkeypatch):
    commitment = dated_commitment(session)
    monkeypatch.setattr(google_calendar, "is_available", lambda: True)
    monkeypatch.setattr(google_calendar, "build_service", lambda: RejectingService())

    sync_engine.run_sync(session, path=tmp_path / "c.ics")

    [failed] = events(session, type=recorder.CALENDAR_EVENT_FAILED)
    assert failed.entity_id == str(commitment.id)
    assert failed.payload["target"] == "google"
    assert "time zone" in failed.payload["error"]
    [summary] = events(session, type=recorder.CALENDAR_SYNCED)
    assert summary.severity == "warning"


# --- Sent folder --------------------------------------------------------------

def test_the_sent_folder_is_found_by_its_flag_not_its_name():
    """A German Gmail account names it "Gesendet"; the \\Sent flag is the same."""
    flags, name = sent_fetcher.parse_list_line(
        b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Gesendet"'
    )
    assert "\\sent" in flags
    assert name == "[Gmail]/Gesendet"


class FakeImap:
    def __init__(self, lines):
        self.lines = lines

    def list(self):
        return "OK", self.lines


def test_without_the_flag_the_usual_names_are_tried():
    imap = FakeImap([b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Sent Items"'])
    assert sent_fetcher.find_sent_mailbox(imap) == "Sent Items"


def test_only_reply_metadata_is_taken_from_sent_mail():
    raw = (
        b"Message-ID: <reply-1@me.com>\r\nIn-Reply-To: <original@x.com>\r\n"
        b"To: Priya <priya@gmail.com>\r\nDate: Tue, 01 Sep 2026 10:00:00 +0000\r\n\r\n"
    )
    headers = sent_fetcher.parse_sent_headers(raw)
    assert headers.in_reply_to == "original@x.com"
    assert headers.recipient_email == "priya@gmail.com"
    assert headers.sent_at == datetime(2026, 9, 1, 10, 0)


def test_sent_messages_are_stored_once(session):
    item = sent_fetcher.SentHeaders("r1@me.com", "o@x.com", "p@x.com", datetime(2026, 9, 1))
    assert sent_fetcher.store_sent(session, [item]) == 1
    assert sent_fetcher.store_sent(session, [item]) == 0
    assert len(session.scalars(select(SentMessage)).all()) == 1
