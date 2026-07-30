"""Pipeline tests using a fake Ollama client — no live model required."""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src import config as pipeline_config
from src.extraction import prompts
from src.extraction.pipeline import (
    evidence_is_verbatim,
    extract_from_email,
    is_boilerplate_evidence,
)
from src.storage import database
from src.storage.models import Base, Commitment, RawEmail

EMAIL_BODY = (
    "Hi,\n\nPlease submit your final project report by 15th August. "
    "Let me know if you have questions.\n"
)


class FakeClient:
    """Returns canned responses in order; records the prompts it received."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str, system=None, schema=None, **kwargs) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            return '{"commitments": []}'
        return self.responses.pop(0)


def make_email(body: str = EMAIL_BODY, **overrides) -> RawEmail:
    fields = dict(
        id=1,
        message_id="m-1@example.com",
        sender_email="alice@university.edu",
        sender_name="Dr. Alice Chen",
        subject="Project report",
        body_text=body,
        received_at=datetime(2025, 7, 22, 9, 0),
        vip_tier="CRITICAL",
    )
    fields.update(overrides)
    return RawEmail(**fields)


def response_with(**overrides) -> str:
    record = {
        "type": "deadline_on_you",
        "subject": "Submit final project report",
        "deadline": "2025-08-15",
        "counterparty": "Dr. Alice Chen",
        "direction": "outgoing",
        "evidence_quote": "Please submit your final project report by 15th August",
        "confidence": 0.95,
    }
    record.update(overrides)
    return json.dumps({"commitments": [record]})


# --- Evidence verification ------------------------------------------------

def test_evidence_is_verbatim_ignores_case_and_whitespace():
    assert evidence_is_verbatim("please SUBMIT your final   project report", EMAIL_BODY)
    assert evidence_is_verbatim("Please submit your final project report", EMAIL_BODY)


def test_evidence_is_verbatim_rejects_paraphrase_and_empty():
    assert not evidence_is_verbatim("You need to hand in the report", EMAIL_BODY)
    assert not evidence_is_verbatim("", EMAIL_BODY)


def test_evidence_tolerates_added_punctuation_and_markdown():
    """Regression: a real meeting was lost because the model added one full stop."""
    body = (
        "We'll have our sprint planning meeting on *3 August at 10:30 AM* in "
        "*Meeting Room B*."
    )
    quote = "We'll have our sprint planning meeting on *3 August at 10:30 AM*."
    assert evidence_is_verbatim(quote, body)
    # The same sentence without the markdown emphasis must also match.
    assert evidence_is_verbatim(
        "sprint planning meeting on 3 August at 10:30 AM", body
    )


def test_evidence_still_rejects_text_lifted_from_another_email():
    body = (
        "Here are this week's top stories in technology. The industry saw major "
        "changes this quarter. Thanks for reading!"
    )
    assert not evidence_is_verbatim("No action needed from your side.", body)


def test_short_quotes_require_an_exact_match():
    """Two-word quotes are too weak for overlap scoring to be meaningful."""
    assert not evidence_is_verbatim("report soon", EMAIL_BODY)


# --- extract_from_email ---------------------------------------------------

def test_successful_extraction_returns_validated_commitment():
    client = FakeClient(response_with())
    outcome = extract_from_email(client, make_email())

    assert outcome.ok
    assert outcome.retried is False
    assert len(outcome.commitments) == 1
    commitment = outcome.commitments[0]
    assert commitment.subject == "Submit final project report"
    assert commitment.deadline == datetime(2025, 8, 15)
    assert commitment.confidence == 0.95
    assert outcome.discarded == 0


def test_email_with_no_commitments_is_a_clean_result():
    client = FakeClient('{"commitments": []}')
    outcome = extract_from_email(client, make_email())
    assert outcome.ok
    assert outcome.commitments == []


def test_prompt_includes_email_context_for_relative_dates():
    client = FakeClient('{"commitments": []}')
    extract_from_email(client, make_email(), user_email="me@example.com")
    prompt = client.prompts[0]
    # Weekday + date let the model resolve wording like "Friday".
    assert "Tuesday, 22 July 2025" in prompt
    assert "Dr. Alice Chen" in prompt
    assert "me@example.com" in prompt
    assert "final project report" in prompt


def test_malformed_output_triggers_one_corrective_retry():
    client = FakeClient("I think there is a deadline here.", response_with())
    outcome = extract_from_email(client, make_email())

    assert outcome.retried is True
    assert len(client.prompts) == 2
    assert "rejected by the validator" in client.prompts[1]
    assert len(outcome.commitments) == 1
    assert outcome.ok


def test_invalid_record_recovered_by_retry():
    client = FakeClient(response_with(confidence=5.0), response_with())
    outcome = extract_from_email(client, make_email())
    assert outcome.retried is True
    assert len(outcome.commitments) == 1
    assert outcome.commitments[0].confidence == 0.95


def test_pipeline_survives_two_bad_responses_without_crashing():
    client = FakeClient("not json at all", "still not json")
    outcome = extract_from_email(client, make_email())
    assert outcome.commitments == []
    assert not outcome.ok  # reported as a failure, but no exception


def test_fabricated_evidence_is_discarded():
    """A quote absent from the email means the record was invented."""
    client = FakeClient(
        response_with(evidence_quote="You must hand in the report soon")
    )
    outcome = extract_from_email(client, make_email())

    assert outcome.commitments == []
    assert outcome.fabricated_evidence == 1


def test_evidence_copied_from_the_prompt_example_is_discarded():
    """Observed in evaluation: the model leaked few-shot text into a real answer."""
    client = FakeClient(
        response_with(evidence_quote="No action needed from your side.")
    )
    outcome = extract_from_email(client, make_email())

    assert outcome.commitments == []
    assert outcome.fabricated_evidence == 1


@pytest.mark.parametrize(
    "quote",
    [
        "Let me know if you have questions.",
        "let me know if you have any questions",
        "Feel free to reach out anytime.",
        "Hope this helps!",
    ],
)
def test_courtesy_boilerplate_is_not_a_commitment(quote):
    body = f"Please submit the report by 15th August. {quote}"
    client = FakeClient(
        response_with(type="question_pending", evidence_quote=quote)
    )
    outcome = extract_from_email(client, make_email(body=body))

    assert outcome.commitments == []
    assert outcome.boilerplate_dropped == 1


def test_real_question_containing_a_closing_is_kept():
    """Only leading boilerplate is dropped — a genuine question survives."""
    body = "Are you free for the review call? Let me know if you have questions."
    client = FakeClient(
        response_with(
            type="question_pending",
            evidence_quote="Are you free for the review call?",
        )
    )
    outcome = extract_from_email(client, make_email(body=body))

    assert len(outcome.commitments) == 1
    assert outcome.boilerplate_dropped == 0


def test_only_first_response_used_when_it_is_valid():
    client = FakeClient(response_with(), response_with(subject="should not be used"))
    outcome = extract_from_email(client, make_email())
    assert len(client.prompts) == 1
    assert outcome.commitments[0].subject == "Submit final project report"


# --- Storage integration --------------------------------------------------

@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    db = TestSession()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _store(session, **overrides) -> RawEmail:
    email = make_email(**overrides)
    email.id = None
    session.add(email)
    session.flush()
    return email


def test_save_commitment_links_to_source_email(session):
    email = _store(session)
    client = FakeClient(response_with())
    outcome = extract_from_email(client, email)

    saved = database.save_commitment(session, email, outcome.commitments[0])
    session.commit()

    assert saved.email_id == email.id
    # The address comes from the email, never the model.
    assert saved.counterparty_email == "alice@university.edu"
    assert saved.vip_tier == "CRITICAL"
    assert saved.status == "pending"
    assert saved.calendar_synced is False
    assert saved.evidence_quote.startswith("Please submit")


def test_emails_awaiting_extraction_excludes_skip_and_processed(session):
    wanted = _store(session, message_id="a", vip_tier="CRITICAL")
    _store(session, message_id="b", vip_tier="SKIP", processed=True)
    _store(session, message_id="c", vip_tier=None)
    _store(session, message_id="d", vip_tier="IMPORTANT", processed=True)

    pending = database.emails_awaiting_extraction(session)
    assert [e.id for e in pending] == [wanted.id]


def test_delete_commitments_for_email_enables_clean_reextraction(session):
    email = _store(session)
    client = FakeClient(response_with())
    outcome = extract_from_email(client, email)
    database.save_commitment(session, email, outcome.commitments[0])
    session.commit()

    removed = database.delete_commitments_for_email(session, email.id)
    session.commit()
    assert removed == 1
    assert session.query(Commitment).count() == 0


def test_list_commitments_orders_dated_before_undated(session):
    body = (
        "Please submit your final project report by 15th August. "
        "Are you attending the seminar?"
    )
    email = _store(session, body_text=body)
    client = FakeClient(
        json.dumps(
            {
                "commitments": [
                    {
                        "type": "question_pending",
                        "subject": "Reply about seminar",
                        "deadline": "",
                        "evidence_quote": "Are you attending the seminar?",
                        "confidence": 0.8,
                    },
                    {
                        "type": "deadline_on_you",
                        "subject": "Submit report",
                        "deadline": "2025-08-15",
                        "evidence_quote": "Please submit your final project report",
                        "confidence": 0.9,
                    },
                ]
            }
        )
    )
    outcome = extract_from_email(client, email)
    for commitment in outcome.commitments:
        database.save_commitment(session, email, commitment)
    session.commit()

    rows = database.list_commitments(session)
    assert rows[0].deadline is not None
    assert rows[-1].deadline is None


# --- Prompt construction --------------------------------------------------

def test_long_body_is_truncated():
    prompt = prompts.build_extraction_prompt(
        subject="s",
        sender_name="n",
        sender_email="e@x.com",
        body_text="A" * 9000,
        max_body_chars=100,
    )
    assert "[...truncated...]" in prompt
    assert "A" * 200 not in prompt


def test_retry_prompt_lists_the_validation_problems():
    prompt = prompts.build_retry_prompt('{"bad": 1}', ["type: invalid value"])
    assert "type: invalid value" in prompt
    assert '{"bad": 1}' in prompt


def test_date_reference_resolves_weekdays_correctly():
    """Tue 22 Jul 2025: Friday is the 25th and the coming Monday is the 28th."""
    reference = prompts.build_date_reference(datetime(2025, 7, 22, 9, 0))
    assert "today (Tuesday) = 2025-07-22" in reference
    assert "tomorrow (Wednesday) = 2025-07-23" in reference
    assert "Friday = 2025-07-25" in reference
    assert "Monday = 2025-07-28" in reference
    assert "end of this month = 2025-07-31" in reference


def test_date_reference_handles_december_rollover():
    reference = prompts.build_date_reference(datetime(2025, 12, 29))
    assert "end of this month = 2025-12-31" in reference
    assert "2026-01-01" in reference


def test_date_reference_is_included_in_the_prompt():
    client = FakeClient('{"commitments": []}')
    extract_from_email(client, make_email())
    assert "Date reference" in client.prompts[0]
    assert "Friday = 2025-07-25" in client.prompts[0]


def test_low_confidence_commitments_are_discarded(monkeypatch):
    monkeypatch.setattr(pipeline_config, "EXTRACTION_MIN_CONFIDENCE", 0.3)
    client = FakeClient(response_with(confidence=0.1))
    outcome = extract_from_email(client, make_email())

    assert outcome.commitments == []
    assert outcome.low_confidence_discarded == 1


def test_confident_commitments_survive_the_floor(monkeypatch):
    monkeypatch.setattr(pipeline_config, "EXTRACTION_MIN_CONFIDENCE", 0.3)
    client = FakeClient(response_with(confidence=0.9))
    outcome = extract_from_email(client, make_email())

    assert len(outcome.commitments) == 1
    assert outcome.low_confidence_discarded == 0
