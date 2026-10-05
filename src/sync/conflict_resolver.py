"""Detect follow-up emails that revise an existing commitment.

Phase 5 of PROJECT_PLAN.md. Threads repeat themselves: "the report is due
Friday" is followed a day later by "actually, let's push that to Monday". Both
emails yield a commitment, and without this module the calendar would show two
conflicting events for one obligation.

The plan's rule is *same subject + counterparty*. Neither is compared literally —
subject lines get reworded between messages, so subjects are reduced to a set of
meaningful words and compared by overlap (Jaccard), with the threshold in
``config.SYNC_DUPLICATE_THRESHOLD``.

When a duplicate is found the **newer** commitment wins and inherits the older
one's ``ics_uid``. Sharing the UID is what makes a subscriber's calendar revise
the existing event rather than add a second one. The older commitment is marked
``superseded`` rather than deleted, so the original email stays traceable and the
user can still see what the deadline used to be.

Deliberately conservative: merging two genuinely separate deadlines is a worse
failure than leaving two similar events on the calendar, so a match requires the
same commitment type, the same counterparty, and a strong subject overlap.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from src import config
from src.events.recorder import COMMITMENT_SUPERSEDED, email_correlation, record_event
from src.storage import database
from src.storage.models import Commitment

log = logging.getLogger(__name__)

#: Reply/forward markers and filler that carry no meaning when comparing.
_SUBJECT_NOISE = re.compile(r"^\s*((re|fw|fwd|reply)\s*:\s*)+", re.IGNORECASE)

#: Words too common to distinguish one commitment from another.
_STOPWORDS = frozenset({
    "a", "an", "the", "to", "for", "of", "on", "in", "at", "by", "with", "and",
    "or", "is", "are", "be", "will", "your", "my", "our", "their", "it", "this",
    "that", "please", "kindly", "need", "needs", "needed", "you", "we", "us",
    "about", "regarding", "re", "update", "updated", "new", "asap",
})

#: A single shared word is not evidence of anything, however rare it is.
_MIN_MEANINGFUL_TOKENS = 2


def normalize_subject(subject: str | None) -> frozenset[str]:
    """Reduce a subject line to its meaningful lowercase words."""
    if not subject:
        return frozenset()
    text = _SUBJECT_NOISE.sub("", subject.lower())
    words = re.findall(r"[a-z0-9]+", text)
    return frozenset(w for w in words if w not in _STOPWORDS and len(w) > 1)


def subject_similarity(a: str | None, b: str | None) -> float:
    """Jaccard overlap of two subjects' meaningful words, 0.0-1.0.

    Jaccard rather than a plain overlap ratio: "meeting" would otherwise score a
    perfect match against "meeting about the Q3 budget", merging a vague
    commitment into an unrelated specific one.
    """
    tokens_a, tokens_b = normalize_subject(a), normalize_subject(b)
    if not tokens_a or not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return len(tokens_a & tokens_b) / len(union)


def counterparty_key(commitment: Commitment) -> str | None:
    """Identity of the other party, preferring the address over the name."""
    if commitment.counterparty_email:
        return commitment.counterparty_email.strip().lower()
    if commitment.counterparty_name:
        return commitment.counterparty_name.strip().lower()
    return None


def _ordering_time(commitment: Commitment) -> datetime:
    """When this commitment's email arrived — used to decide which is newer."""
    email = commitment.source_email
    received = getattr(email, "received_at", None) if email is not None else None
    return received or commitment.created_at or datetime.min


def is_duplicate(
    older: Commitment, newer: Commitment, threshold: float | None = None
) -> bool:
    """Whether ``newer`` looks like a revision of ``older``."""
    if older.id is not None and older.id == newer.id:
        return False

    # Two commitments pulled from the same email are separate obligations the
    # model found side by side, never a revision of one another.
    if older.email_id is not None and older.email_id == newer.email_id:
        return False

    if older.type != newer.type:
        return False

    older_party, newer_party = counterparty_key(older), counterparty_key(newer)
    if not older_party or older_party != newer_party:
        return False

    tokens = normalize_subject(older.subject) & normalize_subject(newer.subject)
    if len(tokens) < _MIN_MEANINGFUL_TOKENS:
        return False

    limit = config.SYNC_DUPLICATE_THRESHOLD if threshold is None else threshold
    return subject_similarity(older.subject, newer.subject) >= limit


@dataclass
class ConflictResolution:
    """What one conflict-resolution pass changed."""

    superseded: int = 0
    #: ``(superseded_id, winner_id)`` pairs, for logging and tests.
    pairs: list[tuple[int, int]] = field(default_factory=list)
    #: Human-readable lines describing each merge.
    notes: list[str] = field(default_factory=list)


def find_duplicate_groups(
    session: Session, threshold: float | None = None
) -> list[list[Commitment]]:
    """Group open commitments that describe the same underlying obligation.

    Each group is ordered oldest-first, so the last element is the current
    version. Groups of one are not returned.
    """
    commitments = database.open_commitments(session)
    groups: list[list[Commitment]] = []

    for commitment in sorted(commitments, key=_ordering_time):
        for group in groups:
            # Compare against the newest member: a thread revised twice should
            # collapse into one group rather than splitting on the first message.
            if is_duplicate(group[-1], commitment, threshold=threshold):
                group.append(commitment)
                break
        else:
            groups.append([commitment])

    return [g for g in groups if len(g) > 1]


def resolve_conflicts(
    session: Session, threshold: float | None = None
) -> ConflictResolution:
    """Collapse duplicate commitments so each obligation has one calendar event.

    The newest commitment in each group survives and takes over the calendar
    event; the rest are marked ``superseded``. Nothing is deleted.
    """
    resolution = ConflictResolution()

    for group in find_duplicate_groups(session, threshold=threshold):
        *older, winner = group

        # Take over the event identity of the commitment being replaced, so the
        # user's calendar revises that event in place — preserving anything they
        # added to it — instead of deleting one event and creating another. If
        # the winner already owns an event, it keeps its own UID.
        if not winner.ics_uid:
            inherited = next((c.ics_uid for c in older if c.ics_uid), None)
            if inherited:
                winner.ics_uid = inherited

        for stale in older:
            stale.status = "superseded"
            # It no longer owns the event; the winner does.
            stale.calendar_synced = False
            resolution.superseded += 1
            resolution.pairs.append((stale.id, winner.id))
            database.log_sync(
                session, stale.id, action="deleted", status="success"
            )
            resolution.notes.append(
                f"#{stale.id} '{stale.subject}' superseded by #{winner.id}"
            )
            record_event(
                session,
                COMMITMENT_SUPERSEDED,
                f"Replaced by a later email: {stale.subject}",
                entity_type="commitment",
                entity_id=stale.id,
                correlation_id=email_correlation(stale.email_id),
                payload={"superseded_by": winner.id, "winner_email_id": winner.email_id},
            )
            log.info(
                "Commitment #%s superseded by #%s (%s)",
                stale.id, winner.id, winner.subject,
            )

        # Point at the most recent thing this commitment replaced.
        winner.supersedes_id = older[-1].id

    session.flush()
    return resolution
