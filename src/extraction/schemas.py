"""Pydantic validation for LLM extraction output.

This is the reliability layer described in PROJECT_PLAN.md §7: LLM JSON is not
trustworthy by default, so nothing reaches the database until it has passed
through these models. Malformed records are rejected (triggering one corrective
retry in the pipeline) rather than silently stored.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CommitmentType(str, Enum):
    """The four commitment kinds the system extracts (PROJECT_PLAN.md §5)."""

    DEADLINE_ON_YOU = "deadline_on_you"
    DEADLINE_FROM_OTHERS = "deadline_from_others"
    QUESTION_PENDING = "question_pending"
    MEETING = "meeting"


class Direction(str, Enum):
    """Who owes whom: ``outgoing`` = you owe them, ``incoming`` = they owe you."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"


#: Direction is definitionally implied by the type, so we derive it rather than
#: trusting a 3B model to get it right.
_DIRECTION_BY_TYPE = {
    CommitmentType.DEADLINE_ON_YOU: Direction.OUTGOING,
    CommitmentType.DEADLINE_FROM_OTHERS: Direction.INCOMING,
    CommitmentType.QUESTION_PENDING: Direction.OUTGOING,
    CommitmentType.MEETING: Direction.INCOMING,
}

#: Strings models emit to mean "no value".
_NULLISH = {"", "null", "none", "n/a", "na", "unknown", "unspecified", "tbd", "-"}

# Date formats accepted beyond ISO 8601, in order of preference.
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d %Y",
)


def _is_nullish(value: Any) -> bool:
    return value is None or (
        isinstance(value, str) and value.strip().lower() in _NULLISH
    )


def parse_deadline(value: Any) -> datetime | None:
    """Parse an LLM-supplied deadline into a naive-UTC datetime, or ``None``.

    Raises ``ValueError`` for a non-empty value that cannot be understood, which
    is what drives the pipeline's corrective retry.
    """
    if _is_nullish(value):
        return None
    if isinstance(value, datetime):
        return _to_naive_utc(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        raise ValueError(f"deadline must be a string or null, got {type(value).__name__}")

    text = value.strip()
    # Tolerate a trailing 'Z' and space-separated ISO datetimes.
    candidate = text.replace("Z", "+00:00")
    try:
        return _to_naive_utc(datetime.fromisoformat(candidate))
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"could not parse deadline {value!r} as a date")


def _to_naive_utc(value: datetime) -> datetime:
    """Drop the timezone while preserving the stated wall-clock time.

    Deadlines are human-facing calendar times. When an email says "10:00 AM IST"
    the user's calendar must show 10:00, not 04:30 — converting to UTC and then
    storing it naive was technically correct but read as plainly wrong in the
    dashboard. We therefore keep the time exactly as written.
    """
    if value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


class ExtractedCommitment(BaseModel):
    """One validated commitment as returned by the model (schema in §12)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    type: CommitmentType
    subject: str = Field(min_length=1)
    deadline: datetime | None = None
    counterparty: str | None = None
    direction: Direction | None = None
    evidence_quote: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("deadline", mode="before")
    @classmethod
    def _validate_deadline(cls, value: Any) -> datetime | None:
        return parse_deadline(value)

    @field_validator("counterparty", mode="before")
    @classmethod
    def _validate_counterparty(cls, value: Any) -> str | None:
        if _is_nullish(value):
            return None
        return str(value).strip() or None

    @field_validator("confidence", mode="before")
    @classmethod
    def _validate_confidence(cls, value: Any) -> float:
        if _is_nullish(value):
            raise ValueError("confidence is required")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"confidence must be a number, got {value!r}") from None
        # Models frequently emit a percentage ("85") instead of a fraction.
        # Only coerce the unambiguous range: a value like 1.5 or 5.0 is garbage,
        # not "5% confident", and must be rejected rather than quietly rescaled.
        if 10.0 <= number <= 100.0:
            number = number / 100.0
        return number

    @field_validator("type", mode="before")
    @classmethod
    def _validate_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("direction", mode="before")
    @classmethod
    def _validate_direction(cls, value: Any) -> Any:
        if _is_nullish(value):
            return None
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @model_validator(mode="after")
    def _derive_direction(self) -> "ExtractedCommitment":
        """Force direction to agree with the type, which defines it."""
        expected = _DIRECTION_BY_TYPE.get(self.type)
        if expected is not None and self.direction != expected:
            object.__setattr__(self, "direction", expected)
        return self

    @model_validator(mode="after")
    def _reject_placeholder_evidence(self) -> "ExtractedCommitment":
        """Guard against the model echoing the instructions instead of the email."""
        if self.evidence_quote.strip().lower() in _NULLISH:
            raise ValueError("evidence_quote must quote the email")
        return self


class ExtractionResponse(BaseModel):
    """The expected top-level shape: an object holding a list of commitments."""

    commitments: list[ExtractedCommitment] = Field(default_factory=list)


# --- Tolerant response parsing -------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _strip_code_fences(text: str) -> str:
    match = _FENCE_RE.search(text)
    return match.group(1).strip() if match else text.strip()


def _find_json_blob(text: str) -> str | None:
    """Return the first balanced JSON object/array in ``text``, if any."""
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
    return None


def _coerce_to_items(payload: Any) -> list[Any]:
    """Normalise the many shapes a model might return into a list of records."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        # Common wrapper keys the model may invent.
        for key in ("commitments", "items", "results", "extractions", "data"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        # A bare single record.
        if any(k in payload for k in ("type", "subject", "evidence_quote")):
            return [payload]
        return []
    return []


class ParsedExtraction(BaseModel):
    """Outcome of validating one model response."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    commitments: list[ExtractedCommitment] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the response parsed and no record was rejected."""
        return not self.errors


def parse_extraction_response(raw_text: str) -> ParsedExtraction:
    """Parse and validate a raw model response into commitments plus errors.

    An empty list with no errors is a legitimate result — most emails contain no
    commitments at all, and the prompt instructs the model to say so.
    """
    if raw_text is None or not raw_text.strip():
        return ParsedExtraction(errors=["empty response from model"])

    text = _strip_code_fences(raw_text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        blob = _find_json_blob(text)
        if blob is None:
            return ParsedExtraction(errors=["response was not valid JSON"])
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError as exc:
            return ParsedExtraction(errors=[f"response was not valid JSON: {exc}"])

    items = _coerce_to_items(payload)
    commitments: list[ExtractedCommitment] = []
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"item {index}: expected an object, got {type(item).__name__}")
            continue
        try:
            commitments.append(ExtractedCommitment.model_validate(item))
        except Exception as exc:
            errors.append(f"item {index}: {_short_error(exc)}")
    return ParsedExtraction(commitments=commitments, errors=errors)


def _short_error(exc: Exception) -> str:
    """Condense a Pydantic error into one line for logs and retry prompts."""
    messages: list[str] = []
    errors = getattr(exc, "errors", None)
    if callable(errors):
        for err in exc.errors():  # type: ignore[attr-defined]
            location = ".".join(str(part) for part in err.get("loc", ())) or "value"
            messages.append(f"{location}: {err.get('msg', 'invalid')}")
    return "; ".join(messages) if messages else str(exc)


#: JSON schema handed to Ollama for structured output. Deliberately flat (no
#: ``$ref``/``$defs``) because the runtime converts it to a grammar, and unions
#: are avoided — "no deadline" is the empty string, normalised to None above.
EXTRACTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "commitments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [t.value for t in CommitmentType],
                    },
                    "subject": {"type": "string"},
                    "deadline": {"type": "string"},
                    "counterparty": {"type": "string"},
                    "direction": {
                        "type": "string",
                        "enum": [d.value for d in Direction],
                    },
                    "evidence_quote": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "type",
                    "subject",
                    "deadline",
                    "evidence_quote",
                    "confidence",
                ],
            },
        }
    },
    "required": ["commitments"],
}
