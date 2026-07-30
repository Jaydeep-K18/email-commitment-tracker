"""Approve or dismiss the commitments the sync engine is holding back."""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from dashboard import data
from dashboard.components import commitment_card
from src.storage.database import (
    session_scope,
    set_commitment_status,
    set_sync_approval,
)
from src.sync.sync_engine import review_queue, run_sync

st.title("Review queue")

now = datetime.now()


def _approve(commitment_id: int) -> None:
    with session_scope() as session:
        set_sync_approval(session, commitment_id, True)
        # Publish straight away so approving has a visible effect rather than
        # waiting for the next scheduled cycle.
        run_sync(session)
    st.session_state["review_toast"] = f"Approved #{commitment_id} — now on the calendar."
    st.rerun()


def _dismiss(commitment_id: int) -> None:
    with session_scope() as session:
        set_commitment_status(session, commitment_id, "dismissed")
        run_sync(session)
    st.session_state["review_toast"] = f"Dismissed #{commitment_id}."
    st.rerun()


if toast := st.session_state.pop("review_toast", None):
    st.success(toast)

with session_scope() as session:
    pending = data.sort_by_urgency(review_queue(session), now=now)
    questions = data.sort_by_urgency(data.pending_questions(session), now=now)

# --- Awaiting approval ----------------------------------------------------
st.subheader(f"Awaiting approval ({len(pending)})")
st.caption(
    "MONITOR-tier and untiered commitments stay off the calendar until you say "
    "otherwise. CRITICAL and IMPORTANT sync on their own."
)

if not pending:
    st.info("Nothing waiting for approval.")
else:
    if st.button(f"✅ Approve all {len(pending)}", type="primary"):
        with session_scope() as session:
            for commitment in review_queue(session):
                set_sync_approval(session, commitment.id, True)
            run_sync(session)
        st.session_state["review_toast"] = "Approved everything in the queue."
        st.rerun()

    for commitment in pending:
        commitment_card.render(
            commitment,
            now=now,
            show_actions=True,
            on_approve=_approve,
            on_dismiss=_dismiss,
            key_prefix="review",
        )

st.divider()

# --- Pending questions ----------------------------------------------------
st.subheader(f"Questions awaiting a reply ({len(questions)})")
st.caption(
    "Questions are never put on the calendar. Dismiss one once you have replied."
)

if not questions:
    st.info("No open questions.")
else:
    for commitment in questions:
        commitment_card.render(
            commitment,
            now=now,
            show_actions=True,
            on_dismiss=_dismiss,
            key_prefix="review_q",
        )
