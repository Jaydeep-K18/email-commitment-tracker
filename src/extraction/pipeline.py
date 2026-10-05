"""Extraction pipeline: filtered email -> prompt -> Ollama -> validate -> store.

Implements the five steps in PROJECT_PLAN.md §12, including the single
corrective retry when validation rejects the model's first answer.

An extra quality guard beyond the plan: because ``evidence_quote`` is the user's
only way to see *why* a commitment was captured, the pipeline checks that the
quote really occurs in the email body and discounts the confidence when it does
not. That makes paraphrased or invented quotes visible instead of silent.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from src import config
from src.classification.service import apply_classification
from src.events.recorder import (
    COMMITMENT_CREATED,
    EMAIL_ANALYZED,
    EXTRACTION_FAILED,
    email_correlation,
    record_event,
)
from src.extraction import prompts
from src.extraction.ollama_client import OllamaClient, OllamaError
from src.extraction.schemas import (
    EXTRACTION_JSON_SCHEMA,
    ExtractedCommitment,
    parse_extraction_response,
)
from src.storage.database import (
    delete_commitments_for_email,
    emails_awaiting_extraction,
    save_commitment,
    session_scope,
)
from src.storage.models import RawEmail

log = logging.getLogger(__name__)

#: Courtesy boilerplate the model repeatedly mistakes for a real question. The
#: prompt asks it to ignore these; enforcing it in code makes it reliable.
_BOILERPLATE_OPENERS = (
    "let me know if you have",
    "let me know if there",
    "let me know if anything",
    "feel free to reach out",
    "feel free to ask",
    "do not hesitate",
    "don't hesitate",
    "hope this helps",
    "hope that helps",
    "no action needed",
    "no action is needed",
    "thanks for reading",
    "for your reference",
    "just letting you know",
)


@dataclass
class ExtractionOutcome:
    """Result of running extraction on one email."""

    email_id: int
    commitments: list[ExtractedCommitment] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: Records dropped because the quote was not in the email.
    fabricated_evidence: int = 0
    #: Records dropped as polite boilerplate.
    boilerplate_dropped: int = 0
    #: Records dropped below the confidence floor.
    low_confidence_discarded: int = 0
    retried: bool = False
    duration_seconds: float = 0.0

    @property
    def discarded(self) -> int:
        return (
            self.fabricated_evidence
            + self.boilerplate_dropped
            + self.low_confidence_discarded
        )

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class PipelineStats:
    """Aggregate result of one extraction run."""

    emails_processed: int = 0
    emails_failed: int = 0
    commitments_stored: int = 0
    retries: int = 0
    discarded: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0


_PUNCTUATION_RE = re.compile(r"[^\w\s]")

#: Fraction of a quote's words that must occur in the email for it to count as
#: grounded when an exact match fails.
EVIDENCE_TOKEN_THRESHOLD = 0.8


def _normalize(text: str) -> str:
    """Lower-case and collapse whitespace runs for comparison."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _strip_punctuation(text: str) -> str:
    """Drop punctuation and markdown emphasis, keeping word boundaries."""
    return re.sub(r"\s+", " ", _PUNCTUATION_RE.sub(" ", _normalize(text))).strip()


def evidence_is_verbatim(quote: str, body: str) -> bool:
    """True if ``quote`` is genuinely grounded in ``body``.

    Exact matching alone proved too brittle in testing: the model quoted a real
    sentence but added a trailing full stop, and a genuine meeting was thrown
    away over that single character. So the check degrades in stages —
    punctuation-insensitive matching first, then a word-overlap threshold — which
    still rejects invented text (a fabricated quote shares almost no words with
    the email) without discarding faithful quotes that differ in punctuation.
    """
    normalized_quote = _normalize(quote)
    if not normalized_quote:
        return False
    if normalized_quote in _normalize(body):
        return True

    # Tolerate added/removed punctuation and markdown emphasis (*bold*).
    stripped_quote = _strip_punctuation(quote)
    if stripped_quote and stripped_quote in _strip_punctuation(body):
        return True

    quote_words = stripped_quote.split()
    if len(quote_words) < 3:
        return False
    body_words = set(_strip_punctuation(body).split())
    matched = sum(1 for word in quote_words if word in body_words)
    return matched / len(quote_words) >= EVIDENCE_TOKEN_THRESHOLD


def is_boilerplate_evidence(quote: str) -> bool:
    """True if the quote is just a polite closing rather than a real commitment."""
    normalized = _normalize(quote).lstrip("\"'")
    return normalized.startswith(_BOILERPLATE_OPENERS)


@dataclass
class _FilterCounts:
    fabricated: int = 0
    boilerplate: int = 0
    low_confidence: int = 0


def _filter_commitments(
    commitments: list[ExtractedCommitment], body: str
) -> tuple[list[ExtractedCommitment], _FilterCounts]:
    """Drop records that cannot be trusted, in order of severity.

    1. **Fabricated evidence** — the quote does not occur in the email. Observed
       in evaluation: the model copied a sentence out of the prompt's own example
       into an unrelated email. Such a record fails the plan's definition of
       ``evidence_quote`` outright, so it is discarded rather than stored.
    2. **Boilerplate** — a polite closing mistaken for a question.
    3. **Low confidence** — the model's own signal that it is guessing.
    """
    counts = _FilterCounts()
    kept: list[ExtractedCommitment] = []

    for commitment in commitments:
        quote = commitment.evidence_quote
        if not evidence_is_verbatim(quote, body):
            counts.fabricated += 1
            log.warning(
                "Discarding commitment: evidence quote is not in the email: %r",
                quote[:80],
            )
            continue
        if is_boilerplate_evidence(quote):
            counts.boilerplate += 1
            log.info("Discarding commitment: courtesy boilerplate: %r", quote[:80])
            continue
        if commitment.confidence < config.EXTRACTION_MIN_CONFIDENCE:
            counts.low_confidence += 1
            log.info(
                "Discarding commitment below confidence %.2f: %r",
                config.EXTRACTION_MIN_CONFIDENCE, commitment.subject[:60],
            )
            continue
        kept.append(commitment)

    return kept, counts


def extract_from_email(
    client: OllamaClient, email: RawEmail, user_email: str | None = None
) -> ExtractionOutcome:
    """Run the model on one email and return validated commitments.

    Retries once with a corrective prompt if the first response fails validation
    (PROJECT_PLAN.md §12, step 3).
    """
    started = time.time()
    outcome = ExtractionOutcome(email_id=email.id)

    prompt = prompts.build_extraction_prompt(
        subject=email.subject,
        sender_name=email.sender_name,
        sender_email=email.sender_email,
        body_text=email.body_text or "",
        received_at=email.received_at,
        user_email=user_email or config.IMAP_USER,
        max_body_chars=config.EXTRACTION_MAX_BODY_CHARS,
    )

    raw = client.generate(
        prompt=prompt,
        system=prompts.SYSTEM_PROMPT,
        schema=EXTRACTION_JSON_SCHEMA,
    )
    parsed = parse_extraction_response(raw)

    if not parsed.ok:
        log.info(
            "Validation failed for email %s (%s); retrying once.",
            email.id, "; ".join(parsed.errors[:2]),
        )
        outcome.retried = True
        retry_prompt = (
            prompt
            + "\n\n"
            + prompts.build_retry_prompt(raw, parsed.errors)
        )
        raw_retry = client.generate(
            prompt=retry_prompt,
            system=prompts.SYSTEM_PROMPT,
            schema=EXTRACTION_JSON_SCHEMA,
        )
        retry_parsed = parse_extraction_response(raw_retry)
        # Keep whichever attempt produced usable records.
        if retry_parsed.ok or retry_parsed.commitments:
            parsed = retry_parsed
        outcome.errors = list(retry_parsed.errors)
    else:
        outcome.errors = []

    commitments, counts = _filter_commitments(
        parsed.commitments, email.body_text or ""
    )
    outcome.commitments = commitments
    outcome.fabricated_evidence = counts.fabricated
    outcome.boilerplate_dropped = counts.boilerplate
    outcome.low_confidence_discarded = counts.low_confidence
    outcome.duration_seconds = time.time() - started
    return outcome


def analyze_and_store(
    session: Session,
    client: OllamaClient,
    email: RawEmail,
    *,
    replace_existing: bool = False,
) -> ExtractionOutcome:
    """Extract, store, and record one email's analysis — all in ``session``.

    This is the unit of work both the background loop and the job system run,
    so the two cannot drift apart. Everything it writes — the commitments, the
    ``processed`` flag, the refined category and the events describing them —
    commits together, so a crash halfway leaves the email unprocessed and
    eligible for a clean retry rather than half-analysed.

    Raises whatever extraction raised; recording the failure is the caller's
    job, in its own transaction, because this one is about to roll back.
    """
    outcome = extract_from_email(client, email)

    if replace_existing:
        delete_commitments_for_email(session, email.id)

    correlation = email_correlation(email.id)
    for extracted in outcome.commitments:
        stored = save_commitment(session, email, extracted)
        due = f", due {stored.deadline:%d %b %H:%M}" if stored.deadline else ""
        record_event(
            session,
            COMMITMENT_CREATED,
            f"Found: {stored.subject}{due}",
            entity_type="commitment",
            entity_id=stored.id,
            correlation_id=correlation,
            payload={
                "type": stored.type,
                "deadline": stored.deadline.isoformat() if stored.deadline else None,
                "confidence": stored.confidence,
            },
        )

    email.processed = True
    found = len(outcome.commitments)
    record_event(
        session,
        EMAIL_ANALYZED,
        (
            f"Analyzed in {outcome.duration_seconds:.1f}s: "
            f"{found} commitment{'s' if found != 1 else ''} found"
            + (" (after a corrective retry)" if outcome.retried else "")
        ),
        entity_type="email",
        entity_id=email.id,
        correlation_id=correlation,
        severity="success",
        payload={
            "commitments": found,
            "retried": outcome.retried,
            "discarded": outcome.discarded,
            "duration_ms": round(outcome.duration_seconds * 1000),
        },
    )
    apply_classification(
        session, email, commitment_types=tuple(c.type.value for c in outcome.commitments)
    )
    return outcome


def record_extraction_failure(email_id: int, exc: BaseException) -> None:
    """Record a failed analysis in a transaction of its own.

    Separate because the analysis transaction is being rolled back, and would
    take the evidence of its own failure with it.
    """
    with session_scope() as session:
        record_event(
            session,
            EXTRACTION_FAILED,
            f"Analysis failed: {exc}",
            entity_type="email",
            entity_id=email_id,
            correlation_id=email_correlation(email_id),
            severity="error",
            payload={"error": str(exc), "error_type": type(exc).__name__},
        )


def process_pending_emails(
    limit: int | None = None,
    client: OllamaClient | None = None,
    replace_existing: bool = False,
) -> PipelineStats:
    """Extract commitments from every VIP-filtered email awaiting processing.

    Emails are marked ``processed`` only when extraction succeeds, so a transient
    failure leaves the email queued for the next run.
    """
    client = client or OllamaClient()
    client.ensure_ready()

    started = time.time()
    stats = PipelineStats()

    with session_scope() as session:
        pending = emails_awaiting_extraction(session, limit=limit)
        email_ids = [e.id for e in pending]

    if not email_ids:
        log.info("No emails awaiting extraction.")
        return stats

    log.info("Extracting from %d email(s)...", len(email_ids))

    for index, email_id in enumerate(email_ids, start=1):
        with session_scope() as session:
            email = session.get(RawEmail, email_id)
            if email is None:
                continue

            log.info(
                "[%d/%d] %s — %s",
                index, len(email_ids),
                email.sender_email or "?",
                (email.subject or "(no subject)")[:60],
            )
            try:
                outcome = analyze_and_store(
                    session, client, email, replace_existing=replace_existing
                )
            except OllamaError as exc:
                # The model/server is unhealthy; stop rather than burn the queue.
                log.error("Ollama failure on email %s: %s", email_id, exc)
                stats.emails_failed += 1
                record_extraction_failure(email_id, exc)
                raise
            except Exception as exc:
                log.warning("Extraction failed for email %s: %s", email_id, exc)
                stats.emails_failed += 1
                record_extraction_failure(email_id, exc)
                continue

            for commitment in outcome.commitments:
                stats.by_type[commitment.type.value] = (
                    stats.by_type.get(commitment.type.value, 0) + 1
                )
                stats.commitments_stored += 1

            stats.emails_processed += 1
            stats.retries += 1 if outcome.retried else 0
            stats.discarded += outcome.discarded

            log.info(
                "    -> %d commitment(s) in %.1fs%s",
                len(outcome.commitments),
                outcome.duration_seconds,
                " (retried)" if outcome.retried else "",
            )

    stats.duration_seconds = time.time() - started
    return stats
