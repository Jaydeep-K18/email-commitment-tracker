"""Today's snapshot — what needs attention right now."""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from dashboard import data, styles
from dashboard.components import commitment_card
from src import config
from src.storage.database import session_scope
from src.sync.sync_engine import calendar_commitments

st.title("Overview")
st.caption("Everything below was extracted on this machine. No email left it.")

now = datetime.now()

with session_scope() as session:
    snapshot = data.build_snapshot(session, now=now)
    upcoming = data.sort_by_urgency(calendar_commitments(session), now=now)[:5]
    questions = data.pending_questions(session)[:5]

if snapshot.total_open == 0:
    st.info(
        "No commitments yet. Use **Fetch now** in the sidebar to pull recent "
        "email, or run `python -m scripts.run_extraction` if emails are already "
        "stored."
    )
    st.stop()

# --- Headline numbers -----------------------------------------------------
# One responsive grid rather than two rows of st.metric: the tiles carry the
# same eight numbers, but a shared accent rule ties each one to the urgency
# colour it represents.
styles.stat_row([
    styles.Stat("Open commitments", snapshot.total_open, tone="primary"),
    styles.Stat(
        "Overdue",
        snapshot.overdue,
        tone=data.OVERDUE,
        hint="needs action" if snapshot.overdue else None,
        loud_hint=True,
    ),
    styles.Stat("Due today", snapshot.due_today, tone=data.TODAY),
    styles.Stat("Due this week", snapshot.due_this_week, tone=data.URGENT),
])
styles.stat_row([
    styles.Stat("On the calendar", snapshot.on_calendar, tone=data.UPCOMING),
    styles.Stat(
        "Awaiting your approval", snapshot.awaiting_approval, tone=data.URGENT
    ),
    styles.Stat("You owe", snapshot.you_owe, tone="primary"),
    styles.Stat("Owed to you", snapshot.owed_to_you, tone="neutral"),
])

if snapshot.awaiting_approval:
    st.warning(
        f"{snapshot.awaiting_approval} commitment(s) are held back until you "
        f"approve them — see **Review queue**."
    )

st.divider()

# --- What's next ----------------------------------------------------------
left, right = st.columns(2, gap="medium")

with left:
    with styles.panel(
        "Next on your calendar",
        key="ect-panel-upcoming",
        icon="📅",
        count=len(upcoming),
    ):
        if not upcoming:
            st.caption("Nothing is currently published to the calendar.")
        for commitment in upcoming:
            commitment_card.render(commitment, now=now, key_prefix="overview")

with right:
    with styles.panel(
        "Questions awaiting a reply",
        key="ect-panel-questions",
        icon="❓",
        count=len(questions),
        caption=(
            "Questions never go on the calendar — this is the only place they "
            "appear."
        )
        if questions
        else None,
    ):
        if not questions:
            st.caption("No open questions.")
        for commitment in questions:
            commitment_card.render(commitment, now=now, key_prefix="overview_q")

st.divider()
st.caption(
    f"Calendar feed: http://{config.SERVER_HOST}:{config.SERVER_PORT}/calendar.ics "
    "(the calendar server must be running for your calendar app to see it)"
)
