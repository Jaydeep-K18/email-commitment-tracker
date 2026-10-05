"""Apply categories to stored emails, recording each change as an event."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.classification.classifier import (
    SOURCE_USER,
    EmailSignals,
    classify,
)
from src.events.recorder import EMAIL_CLASSIFIED, email_correlation, record_event
from src.storage.models import Commitment, RawEmail

#: How each category reads in a sentence on the activity timeline.
CATEGORY_LABELS = {
    "important": "Important",
    "action_required": "Action Required",
    "meeting": "Meetings",
    "update": "Updates",
    "low_priority": "Low Priority",
}


def commitment_types_for(session: Session, email_id: int) -> tuple[str, ...]:
    """Types of the live commitments extracted from one email."""
    rows = session.scalars(
        select(Commitment.type).where(
            Commitment.email_id == email_id, Commitment.status != "superseded"
        )
    )
    return tuple(rows)


def apply_classification(
    session: Session,
    email: RawEmail,
    commitment_types: tuple[str, ...] | None = None,
) -> bool:
    """(Re)classify one email. Returns whether its category changed.

    A category the user chose is final: automatic passes never overwrite it,
    however strongly the rules disagree. Re-running on an email whose category
    and reason are unchanged writes nothing and records nothing, so this is
    safe to call after every extraction.
    """
    if email.category_source == SOURCE_USER:
        return False
    if commitment_types is None:
        commitment_types = commitment_types_for(session, email.id)

    result = classify(EmailSignals.from_email(email, commitment_types))
    if (email.category, email.category_reason) == (result.category, result.reason):
        return False

    previous = email.category
    email.category = result.category
    email.category_source = result.source
    email.category_reason = result.reason

    label = CATEGORY_LABELS[result.category]
    if previous is None:
        message = f"Sorted into {label}: {result.reason}"
    else:
        message = f"Moved from {CATEGORY_LABELS.get(previous, previous)} to {label}: {result.reason}"

    record_event(
        session,
        EMAIL_CLASSIFIED,
        message,
        entity_type="email",
        entity_id=email.id,
        correlation_id=email_correlation(email.id),
        payload={
            "category": result.category,
            "previous": previous,
            "source": result.source,
            "reason": result.reason,
        },
    )
    return True


def classify_unclassified(session: Session, limit: int = 500) -> int:
    """Categorise emails that have none yet — new installs and migrated data."""
    emails = session.scalars(
        select(RawEmail).where(RawEmail.category.is_(None)).order_by(RawEmail.id).limit(limit)
    ).all()
    changed = sum(apply_classification(session, email) for email in emails)
    session.flush()
    return changed
