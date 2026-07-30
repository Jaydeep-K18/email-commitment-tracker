"""Unit tests for VIP filtering (Phase 2)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.filtering import vip_filter
from src.filtering.vip_filter import (
    CRITICAL,
    IMPORTANT,
    MONITOR,
    SKIP,
    TierDecision,
    VipFilterError,
    assign_tier,
    normalize_match_type,
    normalize_match_value,
    normalize_tier,
    rule_matches,
)
from src.storage import database
from src.storage.models import Base, RawEmail, VipContact


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


def rule(value: str, match_type: str, tier: str, rule_id: int = 1) -> VipContact:
    """Build an unsaved rule for pure-matching tests."""
    return VipContact(
        id=rule_id, match_value=value, match_type=match_type, tier=tier
    )


# --- Match types ----------------------------------------------------------

def test_exact_email_matches_case_insensitively():
    r = rule("ada@university.edu", "exact_email", CRITICAL)
    assert rule_matches(r, "ADA@University.EDU", "Ada") is True
    assert rule_matches(r, "bob@university.edu", "Bob") is False


def test_domain_matches_domain_and_subdomains():
    r = rule("university.edu", "domain", IMPORTANT)
    assert rule_matches(r, "anyone@university.edu", None) is True
    assert rule_matches(r, "prof@cs.university.edu", None) is True
    # Must not match a domain that merely ends with the same letters.
    assert rule_matches(r, "spam@notuniversity.edu", None) is False
    assert rule_matches(r, "someone@other.com", None) is False


def test_domain_rule_tolerates_leading_at_sign():
    r = rule("@university.edu", "domain", IMPORTANT)
    assert rule_matches(r, "ada@university.edu", None) is True


def test_name_pattern_substring_match():
    r = rule("lovelace", "name_pattern", CRITICAL)
    assert rule_matches(r, "x@y.com", "Professor Ada Lovelace") is True
    assert rule_matches(r, "x@y.com", "Bob Smith") is False


def test_name_pattern_wildcard_match():
    r = rule("prof*", "name_pattern", CRITICAL)
    assert rule_matches(r, "x@y.com", "Professor Ada") is True
    assert rule_matches(r, "x@y.com", "Ada Professor") is False


def test_rules_do_not_match_missing_sender_fields():
    assert rule_matches(rule("a@b.com", "exact_email", CRITICAL), None, None) is False
    assert rule_matches(rule("b.com", "domain", CRITICAL), None, None) is False
    assert rule_matches(rule("ada", "name_pattern", CRITICAL), "a@b.com", None) is False


def test_unknown_match_type_never_matches():
    assert rule_matches(rule("x", "bogus", CRITICAL), "x@y.com", "X") is False


# --- Tier assignment and precedence --------------------------------------

def test_unknown_sender_defaults_to_skip():
    decision = assign_tier([], "stranger@nowhere.com", "Stranger")
    assert decision.tier == SKIP
    assert decision.matched is False
    assert decision.should_process is False


def test_matched_critical_sender_should_process():
    rules = [rule("ada@university.edu", "exact_email", CRITICAL)]
    decision = assign_tier(rules, "ada@university.edu", "Ada")
    assert decision == TierDecision(tier=CRITICAL, rule=rules[0])
    assert decision.should_process is True


def test_exact_email_rule_overrides_broader_domain_rule():
    """A specific SKIP must beat a broad CRITICAL for the same sender."""
    rules = [
        rule("university.edu", "domain", CRITICAL, rule_id=1),
        rule("noreply@university.edu", "exact_email", SKIP, rule_id=2),
    ]
    assert assign_tier(rules, "noreply@university.edu", None).tier == SKIP
    # Other senders on the domain still get CRITICAL.
    assert assign_tier(rules, "ada@university.edu", None).tier == CRITICAL


def test_domain_rule_overrides_name_pattern_rule():
    rules = [
        rule("ada", "name_pattern", MONITOR, rule_id=1),
        rule("university.edu", "domain", CRITICAL, rule_id=2),
    ]
    assert assign_tier(rules, "ada@university.edu", "Ada").tier == CRITICAL


def test_highest_tier_wins_among_equally_specific_rules():
    rules = [
        rule("university.edu", "domain", MONITOR, rule_id=1),
        rule("cs.university.edu", "domain", CRITICAL, rule_id=2),
    ]
    assert assign_tier(rules, "ada@cs.university.edu", None).tier == CRITICAL


# --- Validation -----------------------------------------------------------

def test_normalize_tier_accepts_any_case_and_rejects_invalid():
    assert normalize_tier("critical") == CRITICAL
    assert normalize_tier(" Important ") == IMPORTANT
    with pytest.raises(VipFilterError):
        normalize_tier("URGENT")


def test_normalize_match_type_rejects_invalid():
    assert normalize_match_type("EXACT_EMAIL") == "exact_email"
    with pytest.raises(VipFilterError):
        normalize_match_type("regex")


def test_normalize_match_value_lowercases_and_strips():
    assert normalize_match_value(" ADA@Uni.EDU ", "exact_email") == "ada@uni.edu"
    assert normalize_match_value("@Uni.EDU", "domain") == "uni.edu"
    # Name patterns keep their original case.
    assert normalize_match_value(" Prof Ada ", "name_pattern") == "Prof Ada"
    with pytest.raises(VipFilterError):
        normalize_match_value("   ", "exact_email")


# --- Database-backed behaviour -------------------------------------------

def test_add_vip_contact_normalizes_and_updates_existing(session):
    created = database.add_vip_contact(
        session, "ADA@Uni.EDU", "exact_email", "critical", display_name="Ada"
    )
    session.commit()
    assert created.match_value == "ada@uni.edu"
    assert created.tier == CRITICAL

    # Same rule again updates the tier rather than duplicating.
    updated = database.add_vip_contact(session, "ada@uni.edu", "exact_email", MONITOR)
    session.commit()
    assert updated.id == created.id
    assert updated.tier == MONITOR
    assert len(database.list_vip_contacts(session)) == 1


def test_add_vip_contact_rejects_bad_tier(session):
    with pytest.raises(VipFilterError):
        database.add_vip_contact(session, "a@b.com", "exact_email", "SUPER")


def test_delete_vip_contact(session):
    contact = database.add_vip_contact(session, "a@b.com", "exact_email", CRITICAL)
    session.commit()
    assert database.delete_vip_contact(session, contact.id) is True
    assert database.delete_vip_contact(session, 9999) is False
    assert database.list_vip_contacts(session) == []


def _store_email(session, message_id: str, sender: str, name: str | None = None):
    email = RawEmail(
        message_id=message_id, sender_email=sender, sender_name=name, subject="s"
    )
    session.add(email)
    session.flush()
    return email


def test_apply_tiers_tags_emails_and_closes_out_skips(session):
    database.add_vip_contact(session, "ada@uni.edu", "exact_email", CRITICAL)
    vip = _store_email(session, "m1", "ada@uni.edu", "Ada")
    stranger = _store_email(session, "m2", "spam@nowhere.com", "Spam")
    session.commit()

    counts = vip_filter.apply_tiers_to_stored_emails(session)
    session.commit()

    assert counts["total"] == 2
    assert counts[CRITICAL] == 1
    assert counts[SKIP] == 1

    assert vip.vip_tier == CRITICAL
    # A VIP email stays open for the Phase 3 extraction pipeline.
    assert vip.processed is False

    assert stranger.vip_tier == SKIP
    # SKIP senders are closed out so extraction ignores them.
    assert stranger.processed is True


def test_apply_tiers_only_touches_untagged_by_default(session):
    email = _store_email(session, "m1", "ada@uni.edu", "Ada")
    session.commit()

    # First pass: no rules yet, so it becomes SKIP.
    vip_filter.apply_tiers_to_stored_emails(session)
    session.commit()
    assert email.vip_tier == SKIP

    database.add_vip_contact(session, "ada@uni.edu", "exact_email", CRITICAL)
    session.commit()

    # Default pass skips already-tagged rows...
    counts = vip_filter.apply_tiers_to_stored_emails(session)
    assert counts["total"] == 0
    assert email.vip_tier == SKIP

    # ...but retag_all re-evaluates it against the new rule.
    counts = vip_filter.apply_tiers_to_stored_emails(session, retag_all=True)
    session.commit()
    assert counts["total"] == 1
    assert email.vip_tier == CRITICAL


def test_tier_for_sender_uses_stored_rules(session):
    database.add_vip_contact(session, "uni.edu", "domain", IMPORTANT)
    session.commit()
    assert vip_filter.tier_for_sender(session, "anyone@uni.edu", None).tier == IMPORTANT
    assert vip_filter.tier_for_sender(session, "x@other.com", None).tier == SKIP
