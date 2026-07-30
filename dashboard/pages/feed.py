"""The full commitment list, with filters and evidence drill-down."""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from dashboard import data
from dashboard.components import commitment_card
from src.filtering.vip_filter import TIERS
from src.storage.database import list_commitments, session_scope

st.title("Commitments")

now = datetime.now()

# --- Filters --------------------------------------------------------------
with st.container(border=True):
    first, second = st.columns(2)
    with first:
        types = st.multiselect(
            "Type",
            options=list(data.TYPE_LABEL),
            format_func=data.type_label,
            placeholder="All types",
        )
        urgencies = st.multiselect(
            "Urgency",
            options=list(data.URGENCY_STYLE),
            format_func=lambda u: data.URGENCY_STYLE[u][1],
            placeholder="Any urgency",
        )
    with second:
        tiers = st.multiselect(
            "Tier", options=[*TIERS, "untiered"], placeholder="All tiers"
        )
        search = st.text_input(
            "Search", placeholder="subject, person, or evidence text"
        )

    show_closed = st.checkbox(
        "Include dismissed and superseded", value=False,
        help="Superseded commitments were replaced by a later email.",
    )

with session_scope() as session:
    commitments = data.feed_commitments(
        session,
        include_closed=show_closed,
        types=types,
        tiers=tiers,
        urgencies=urgencies,
        search=search,
        now=now,
    )
    total = len(list_commitments(session))

if not commitments:
    st.info(
        "No commitments match these filters."
        if total
        else "No commitments extracted yet."
    )
    st.stop()

st.caption(f"Showing {len(commitments)} of {total} commitment(s)")

# --- Grouped by urgency ---------------------------------------------------
for band in (data.OVERDUE, data.TODAY, data.URGENT, data.UPCOMING, data.UNDATED):
    group = [c for c in commitments if data.urgency(c.deadline, now=now) == band]
    if not group:
        continue
    colour, label = data.URGENCY_STYLE[band]
    st.markdown(
        f"<h4 style='color:{colour};margin-bottom:0.2rem'>"
        f"{data.URGENCY_ICON[band]} {label} ({len(group)})</h4>",
        unsafe_allow_html=True,
    )
    for commitment in group:
        commitment_card.render(commitment, now=now, key_prefix=f"feed_{band}")
