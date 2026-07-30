"""Validation-layer tests: the guard between LLM output and the database."""
from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from src.extraction.schemas import (
    CommitmentType,
    Direction,
    ExtractedCommitment,
    parse_deadline,
    parse_extraction_response,
)


def valid_payload(**overrides):
    payload = {
        "type": "deadline_on_you",
        "subject": "Submit final report",
        "deadline": "2025-08-15",
        "counterparty": "Dr. Alice Chen",
        "direction": "outgoing",
        "evidence_quote": "please submit your final project report by 15th August",
        "confidence": 0.95,
    }
    payload.update(overrides)
    return payload


# --- Accepting valid records ---------------------------------------------

def test_valid_record_is_accepted():
    commitment = ExtractedCommitment.model_validate(valid_payload())
    assert commitment.type is CommitmentType.DEADLINE_ON_YOU
    assert commitment.deadline == datetime(2025, 8, 15)
    assert commitment.confidence == 0.95


def test_type_is_case_insensitive():
    commitment = ExtractedCommitment.model_validate(valid_payload(type="MEETING"))
    assert commitment.type is CommitmentType.MEETING


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2025-08-15", datetime(2025, 8, 15)),
        ("2025-08-15T17:30:00", datetime(2025, 8, 15, 17, 30)),
        ("2025-08-15 17:30", datetime(2025, 8, 15, 17, 30)),
        ("15 August 2025", datetime(2025, 8, 15)),
        ("August 15, 2025", datetime(2025, 8, 15)),
    ],
)
def test_various_date_formats_parse(raw, expected):
    assert parse_deadline(raw) == expected


def test_timezone_aware_deadline_keeps_the_stated_wall_clock_time():
    """"10:00 AM IST" must stay 10:00 — a calendar shows local time, not UTC."""
    assert parse_deadline("2026-08-08T10:00:00+05:30") == datetime(2026, 8, 8, 10, 0)
    assert parse_deadline("2025-08-15T12:00:00+02:00") == datetime(2025, 8, 15, 12, 0)
    assert parse_deadline("2025-08-15T10:00:00Z") == datetime(2025, 8, 15, 10, 0)


@pytest.mark.parametrize("nullish", ["", "null", "none", "N/A", "unknown", None])
def test_nullish_deadline_becomes_none(nullish):
    assert parse_deadline(nullish) is None


def test_percentage_confidence_is_converted_to_fraction():
    commitment = ExtractedCommitment.model_validate(valid_payload(confidence=85))
    assert commitment.confidence == 0.85
    assert ExtractedCommitment.model_validate(
        valid_payload(confidence=100)
    ).confidence == 1.0


@pytest.mark.parametrize("garbage", [1.5, 5.0, 9.0])
def test_ambiguous_confidence_is_rejected_not_silently_rescaled(garbage):
    """5.0 is broken output, not "5% confident" — never invent a plausible value."""
    with pytest.raises(ValidationError):
        ExtractedCommitment.model_validate(valid_payload(confidence=garbage))


def test_direction_is_derived_from_type_when_model_disagrees():
    # The model claims "incoming" but deadline_on_you is definitionally outgoing.
    commitment = ExtractedCommitment.model_validate(
        valid_payload(type="deadline_on_you", direction="incoming")
    )
    assert commitment.direction is Direction.OUTGOING

    commitment = ExtractedCommitment.model_validate(
        valid_payload(type="deadline_from_others", direction="outgoing")
    )
    assert commitment.direction is Direction.INCOMING


def test_missing_counterparty_is_allowed():
    commitment = ExtractedCommitment.model_validate(valid_payload(counterparty="null"))
    assert commitment.counterparty is None


# --- Rejecting malformed records -----------------------------------------

def test_invalid_type_is_rejected():
    with pytest.raises(ValidationError):
        ExtractedCommitment.model_validate(valid_payload(type="urgent_thing"))


def test_unparseable_deadline_is_rejected():
    with pytest.raises(ValidationError):
        ExtractedCommitment.model_validate(valid_payload(deadline="sometime soon"))


@pytest.mark.parametrize("bad", [-0.5, 1.5, 200, "high"])
def test_out_of_range_confidence_is_rejected(bad):
    with pytest.raises(ValidationError):
        ExtractedCommitment.model_validate(valid_payload(confidence=bad))


def test_empty_evidence_quote_is_rejected():
    with pytest.raises(ValidationError):
        ExtractedCommitment.model_validate(valid_payload(evidence_quote=""))


def test_placeholder_evidence_quote_is_rejected():
    with pytest.raises(ValidationError):
        ExtractedCommitment.model_validate(valid_payload(evidence_quote="N/A"))


def test_empty_subject_is_rejected():
    with pytest.raises(ValidationError):
        ExtractedCommitment.model_validate(valid_payload(subject="  "))


def test_missing_required_field_is_rejected():
    payload = valid_payload()
    del payload["evidence_quote"]
    with pytest.raises(ValidationError):
        ExtractedCommitment.model_validate(payload)


# --- Tolerant response parsing -------------------------------------------

def test_parses_wrapped_object():
    result = parse_extraction_response('{"commitments": [%s]}' % _json(valid_payload()))
    assert result.ok
    assert len(result.commitments) == 1


def test_parses_bare_array():
    result = parse_extraction_response("[%s]" % _json(valid_payload()))
    assert result.ok
    assert len(result.commitments) == 1


def test_parses_single_bare_object():
    result = parse_extraction_response(_json(valid_payload()))
    assert result.ok
    assert len(result.commitments) == 1


def test_parses_markdown_fenced_json():
    text = "Here you go:\n```json\n{\"commitments\": [%s]}\n```" % _json(valid_payload())
    result = parse_extraction_response(text)
    assert result.ok
    assert len(result.commitments) == 1


def test_parses_json_embedded_in_prose():
    text = "Sure! {\"commitments\": []} Hope that helps."
    result = parse_extraction_response(text)
    assert result.ok
    assert result.commitments == []


def test_empty_commitment_list_is_valid_not_an_error():
    """Most emails contain nothing — that must not count as a failure."""
    result = parse_extraction_response('{"commitments": []}')
    assert result.ok
    assert result.commitments == []


def test_non_json_response_reports_error():
    result = parse_extraction_response("I could not find any commitments.")
    assert not result.ok
    assert result.commitments == []


def test_empty_response_reports_error():
    assert not parse_extraction_response("   ").ok


def test_one_bad_record_does_not_discard_the_good_ones():
    good = _json(valid_payload())
    bad = _json(valid_payload(type="nonsense"))
    result = parse_extraction_response('{"commitments": [%s, %s]}' % (good, bad))
    assert len(result.commitments) == 1
    assert len(result.errors) == 1
    assert not result.ok


def _json(payload: dict) -> str:
    import json

    return json.dumps(payload)
