"""The commitment network — who owes what to whom.

Thin rendering over :mod:`dashboard.graph`, which holds the graph building and
has no Streamlit dependency.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

from dashboard import data, styles
from dashboard import graph as graph_builder
from src.storage.database import list_commitments, session_scope

st.title("Commitment network")
st.caption(
    "An arrow points the way the obligation runs: from you to someone means "
    "you owe them. Edge colour is urgency; node colour is VIP tier."
)

now = datetime.now()

with session_scope() as session:
    commitments = [
        c
        for c in list_commitments(session)
        if c.status not in ("dismissed", "superseded")
    ]

if not commitments:
    st.info("No commitments to graph yet.")
    st.stop()

people = graph_builder.people_options(commitments)
people_labels = dict(people)

# --- Filters --------------------------------------------------------------
with st.container(border=True):
    left, right = st.columns(2)
    with left:
        selected_people = st.multiselect(
            "People",
            options=[key for key, _ in people],
            format_func=lambda key: people_labels.get(key, key),
            placeholder="Everyone",
        )
    with right:
        selected_types = st.multiselect(
            "Commitment type",
            options=list(data.TYPE_LABEL),
            format_func=data.type_label,
            placeholder="All types",
        )

network = graph_builder.build_graph(
    commitments, people=selected_people, types=selected_types, now=now
)
stats = graph_builder.graph_stats(network)

if stats.commitments == 0:
    st.warning("Nothing matches these filters.")
    st.stop()

# --- Numbers --------------------------------------------------------------
columns = st.columns(4)
columns[0].metric("People", stats.people)
columns[1].metric("Commitments", stats.commitments)
columns[2].metric("You owe", stats.you_owe)
columns[3].metric("Owed to you", stats.owed_to_you)

# --- The graph ------------------------------------------------------------
components.html(
    graph_builder.render_html(network, theme=styles.current_theme()),
    height=640,
    scrolling=False,
)

st.caption(
    "Drag to rearrange · scroll to zoom · hover a node or edge for detail. "
    "Dashed edges are questions awaiting a reply."
)

# --- Legend and highlights ------------------------------------------------
legend, busiest = st.columns(2)

with legend:
    st.subheader("Legend")
    st.markdown("**Edge colour — urgency**")
    for band in (data.OVERDUE, data.TODAY, data.URGENT, data.UPCOMING, data.UNDATED):
        colour, label = data.URGENCY_STYLE[band]
        st.markdown(
            f"<span style='color:{colour};font-size:1.3em'>&#9632;</span> {label}",
            unsafe_allow_html=True,
        )
    st.markdown("**Node colour — VIP tier**")
    for tier, colour in graph_builder.TIER_COLOUR.items():
        st.markdown(
            f"<span style='color:{colour};font-size:1.3em'>&#9679;</span> {tier}",
            unsafe_allow_html=True,
        )

with busiest:
    st.subheader("Most entangled")
    if not stats.busiest:
        st.caption("No contacts to rank.")
    for label, count in stats.busiest:
        st.markdown(f"**{label}** — {count} commitment(s)")
