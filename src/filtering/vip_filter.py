"""VIP filtering — decide which emails are worth processing.

Layer 2 of the architecture (PROJECT_PLAN.md §6). A sender is matched against the
user's ``vip_contacts`` rules and assigned a tier. Emails from unknown senders
default to ``SKIP`` and never reach the local LLM, which keeps extraction cost
focused on email the user actually cares about.

Three rule kinds are supported (schema §10):

``exact_email``   the sender address, compared case-insensitively
``domain``        the sender's domain, including subdomains
``name_pattern``  a substring of the display name, or an fnmatch wildcard
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.storage.models import RawEmail, VipContact

# --- Tiers ---------------------------------------------------------------
CRITICAL = "CRITICAL"
IMPORTANT = "IMPORTANT"
MONITOR = "MONITOR"
SKIP = "SKIP"

TIERS: tuple[str, ...] = (CRITICAL, IMPORTANT, MONITOR, SKIP)

#: Senders with no matching rule are ignored rather than sent to the LLM.
DEFAULT_TIER = SKIP

# Lower rank = higher priority when two rules of equal specificity both match.
_TIER_RANK = {CRITICAL: 0, IMPORTANT: 1, MONITOR: 2, SKIP: 3}

# --- Match types ---------------------------------------------------------
MATCH_EXACT_EMAIL = "exact_email"
MATCH_DOMAIN = "domain"
MATCH_NAME_PATTERN = "name_pattern"

MATCH_TYPES: tuple[str, ...] = (MATCH_EXACT_EMAIL, MATCH_DOMAIN, MATCH_NAME_PATTERN)

# Specificity: an exact address beats a whole domain, which beats a fuzzy name.
_MATCH_TYPE_RANK = {MATCH_EXACT_EMAIL: 0, MATCH_DOMAIN: 1, MATCH_NAME_PATTERN: 2}


class VipFilterError(ValueError):
    """Raised for an invalid tier or match type."""


@dataclass(frozen=True)
class TierDecision:
    """The outcome of filtering one sender."""

    tier: str
    rule: VipContact | None

    @property
    def matched(self) -> bool:
        return self.rule is not None

    @property
    def should_process(self) -> bool:
        """Whether this email should continue to AI extraction (Phase 3)."""
        return self.tier != SKIP


# --- Validation ----------------------------------------------------------

def normalize_tier(value: str) -> str:
    """Return the canonical upper-case tier, or raise :class:`VipFilterError`."""
    candidate = (value or "").strip().upper()
    if candidate not in _TIER_RANK:
        raise VipFilterError(
            f"Invalid tier {value!r}. Expected one of: {', '.join(TIERS)}"
        )
    return candidate


def normalize_match_type(value: str) -> str:
    """Return the canonical lower-case match type, or raise."""
    candidate = (value or "").strip().lower()
    if candidate not in _MATCH_TYPE_RANK:
        raise VipFilterError(
            f"Invalid match type {value!r}. Expected one of: {', '.join(MATCH_TYPES)}"
        )
    return candidate


def normalize_match_value(value: str, match_type: str) -> str:
    """Clean a rule's match value for the given match type.

    Addresses and domains are lower-cased (and a leading ``@`` is dropped from a
    domain); name patterns keep their case since matching is case-insensitive.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        raise VipFilterError("Match value cannot be empty.")
    if match_type == MATCH_EXACT_EMAIL:
        return cleaned.lower()
    if match_type == MATCH_DOMAIN:
        return cleaned.lower().lstrip("@")
    return cleaned


# --- Matching ------------------------------------------------------------

def _domain_of(email_address: str) -> str:
    _, _, domain = email_address.partition("@")
    return domain


def rule_matches(
    rule: VipContact, sender_email: str | None, sender_name: str | None
) -> bool:
    """Return True if ``rule`` applies to this sender."""
    match_type = (rule.match_type or "").strip().lower()
    value = (rule.match_value or "").strip()
    if not value:
        return False

    if match_type == MATCH_EXACT_EMAIL:
        if not sender_email:
            return False
        return sender_email.strip().lower() == value.lower()

    if match_type == MATCH_DOMAIN:
        if not sender_email:
            return False
        target = value.lower().lstrip("@")
        domain = _domain_of(sender_email.strip().lower())
        # Match the domain itself or any subdomain of it.
        return bool(domain) and (domain == target or domain.endswith("." + target))

    if match_type == MATCH_NAME_PATTERN:
        if not sender_name:
            return False
        name = sender_name.strip().lower()
        pattern = value.lower()
        if any(ch in pattern for ch in "*?["):
            return fnmatch(name, pattern)
        return pattern in name

    return False


def _precedence(rule: VipContact) -> tuple[int, int, int]:
    """Sort key: specificity, then tier importance, then insertion order."""
    return (
        _MATCH_TYPE_RANK.get((rule.match_type or "").lower(), 99),
        _TIER_RANK.get((rule.tier or "").upper(), 99),
        rule.id or 0,
    )


def best_match(
    rules: list[VipContact], sender_email: str | None, sender_name: str | None
) -> VipContact | None:
    """Return the single winning rule for a sender, or ``None`` if none match.

    When several rules match, the most specific wins — so an explicit
    ``exact_email`` SKIP rule correctly overrides a broad ``domain`` CRITICAL rule.
    """
    matching = [r for r in rules if rule_matches(r, sender_email, sender_name)]
    if not matching:
        return None
    return min(matching, key=_precedence)


def assign_tier(
    rules: list[VipContact], sender_email: str | None, sender_name: str | None
) -> TierDecision:
    """Resolve a sender to a :class:`TierDecision` (defaults to ``SKIP``)."""
    rule = best_match(rules, sender_email, sender_name)
    if rule is None:
        return TierDecision(tier=DEFAULT_TIER, rule=None)
    return TierDecision(tier=normalize_tier(rule.tier), rule=rule)


# --- Database-facing helpers ---------------------------------------------

def load_rules(session: Session) -> list[VipContact]:
    """Load every VIP rule (small table; cheap to load once per fetch cycle)."""
    return list(session.execute(select(VipContact)).scalars().all())


def tier_for_sender(
    session: Session, sender_email: str | None, sender_name: str | None
) -> TierDecision:
    """Convenience wrapper: load rules and resolve one sender."""
    return assign_tier(load_rules(session), sender_email, sender_name)


def apply_tiers_to_stored_emails(
    session: Session, retag_all: bool = False
) -> dict[str, int]:
    """Tag stored emails with their VIP tier.

    Used after adding or changing rules so already-fetched email is re-evaluated.
    By default only untagged emails (``vip_tier IS NULL``) are touched; pass
    ``retag_all=True`` to re-evaluate every email.

    SKIP-tier emails are marked ``processed`` so the extraction pipeline ignores
    them. Emails already extracted are never reverted to unprocessed.

    Returns per-tier counts plus ``total``.
    """
    stmt = select(RawEmail)
    if not retag_all:
        stmt = stmt.where(RawEmail.vip_tier.is_(None))
    emails = list(session.execute(stmt).scalars().all())

    rules = load_rules(session)
    counts = {tier: 0 for tier in TIERS}
    for email in emails:
        decision = assign_tier(rules, email.sender_email, email.sender_name)
        email.vip_tier = decision.tier
        if decision.tier == SKIP:
            # Nothing to extract from a skipped sender — close it out.
            email.processed = True
        counts[decision.tier] += 1

    session.flush()
    counts["total"] = len(emails)
    return counts
