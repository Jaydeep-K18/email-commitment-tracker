"""Manage the VIP rules that decide which senders are worth processing."""
from __future__ import annotations

import streamlit as st

from src.filtering.vip_filter import (
    MATCH_TYPES,
    TIERS,
    VipFilterError,
    apply_tiers_to_stored_emails,
)
from src.storage.database import (
    add_vip_contact,
    delete_vip_contact,
    list_vip_contacts,
    session_scope,
)

st.title("VIP contacts")
st.caption(
    "Email from senders with no rule is skipped entirely and never reaches the "
    "local LLM. That is what keeps processing focused and fast."
)

_TIER_HELP = {
    "CRITICAL": "Deadlines sync to the calendar automatically.",
    "IMPORTANT": "Syncs automatically, flagged for you to sanity-check.",
    "MONITOR": "Extracted, but held until you approve it.",
    "SKIP": "Ignored completely.",
}

_MATCH_HELP = {
    "exact_email": "One address, e.g. alice@university.edu",
    "domain": "Everyone at a domain, e.g. university.edu",
    "name_pattern": "Part of a display name, e.g. Chen",
}

if toast := st.session_state.pop("vip_toast", None):
    st.success(toast)

# --- Add a rule -----------------------------------------------------------
with st.form("add_vip", clear_on_submit=True):
    st.subheader("Add a rule")
    left, middle, right = st.columns([3, 2, 2])
    match_value = left.text_input(
        "Address, domain, or name", placeholder="alice@university.edu"
    )
    match_type = middle.selectbox(
        "Match type", options=MATCH_TYPES, format_func=lambda t: t.replace("_", " ")
    )
    tier = right.selectbox("Tier", options=TIERS)
    display_name = st.text_input("Display name (optional)", placeholder="Alice Chen")

    st.caption(f"**{match_type}** — {_MATCH_HELP[match_type]}")
    st.caption(f"**{tier}** — {_TIER_HELP[tier]}")

    if st.form_submit_button("Add rule", type="primary"):
        if not match_value.strip():
            st.error("Enter an address, domain, or name first.")
        else:
            try:
                with session_scope() as session:
                    add_vip_contact(
                        session,
                        match_value=match_value,
                        match_type=match_type,
                        tier=tier,
                        display_name=display_name or None,
                    )
                    # retag_all: a new rule must be applied to email already
                    # fetched, not just to email arriving from now on.
                    retagged = apply_tiers_to_stored_emails(session, retag_all=True)
                st.session_state["vip_toast"] = (
                    f"Added {match_value} as {tier}. Re-tagged "
                    f"{retagged['total']} stored email(s)."
                )
                st.rerun()
            except VipFilterError as exc:
                st.error(str(exc))

st.divider()

# --- Existing rules -------------------------------------------------------
with session_scope() as session:
    contacts = list_vip_contacts(session)

st.subheader(f"Current rules ({len(contacts)})")

if not contacts:
    st.info(
        "No rules yet — every sender is being skipped, so nothing will be "
        "extracted. Add at least one rule above."
    )
    st.stop()

for contact in contacts:
    with st.container(border=True):
        details, action = st.columns([5, 1])
        with details:
            label = contact.display_name or contact.match_value
            st.markdown(f"**{label}** · `{contact.tier}`")
            st.caption(
                f"{contact.match_type.replace('_', ' ')}: `{contact.match_value}`"
            )
        with action:
            if st.button("Remove", key=f"vip_delete_{contact.id}",
                         use_container_width=True):
                with session_scope() as session:
                    delete_vip_contact(session, contact.id)
                    apply_tiers_to_stored_emails(session, retag_all=True)
                st.session_state["vip_toast"] = f"Removed {contact.match_value}."
                st.rerun()

st.divider()
if st.button("Re-apply rules to stored email"):
    with session_scope() as session:
        retagged = apply_tiers_to_stored_emails(session, retag_all=True)
    st.session_state["vip_toast"] = (
        f"Re-tagged {retagged['total']} stored email(s)."
    )
    st.rerun()
