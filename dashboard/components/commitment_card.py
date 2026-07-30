"""The commitment card — one commitment rendered consistently everywhere.

Every card can be expanded to show the evidence quote and the source email id.
That drill-down is the point: the user should never have to take an extracted
commitment on faith, and should always be able to see the sentence that caused
it (PROJECT_PLAN.md §12).
"""
from __future__ import annotations

import streamlit as st

from dashboard import data
from src.storage.models import Commitment
from src.sync.sync_engine import decide


def _headline(commitment: Commitment, now=None) -> str:
    icon = data.urgency_icon(commitment.deadline, now=now)
    type_icon = data.TYPE_ICON.get(commitment.type, "•")
    when = data.format_deadline(commitment.deadline)
    relative = data.relative_deadline(commitment.deadline, now=now)
    suffix = f" · {relative}" if relative else ""
    return f"{icon} {type_icon} **{commitment.subject}** — {when}{suffix}"


def render(
    commitment: Commitment,
    *,
    now=None,
    show_actions: bool = False,
    on_approve=None,
    on_dismiss=None,
    key_prefix: str = "card",
) -> None:
    """Render one commitment. Actions are optional so read-only pages stay clean."""
    decision = decide(commitment)

    with st.container(border=True):
        st.markdown(_headline(commitment, now=now))

        who = commitment.counterparty_name or commitment.counterparty_email or "unknown"
        tier = commitment.vip_tier or "untiered"
        meta = [
            data.type_label(commitment.type),
            f"with **{who}**",
            f"tier `{tier}`",
            f"confidence {commitment.confidence:.0%}",
        ]
        st.caption(" · ".join(meta))

        if decision.should_sync:
            st.caption(f"📅 on the calendar — {decision.reason}")
        else:
            st.caption(f"⛔ not on the calendar — {decision.reason}")

        with st.expander("Why was this captured?"):
            st.markdown("**From the email:**")
            st.info(f"“{(commitment.evidence_quote or '').strip()}”")
            details = {
                "Source email": f"#{commitment.email_id}",
                "Status": commitment.status,
                "Direction": commitment.direction or "—",
                "Calendar event": commitment.ics_uid or "not published",
            }
            if commitment.supersedes_id:
                details["Replaces"] = f"commitment #{commitment.supersedes_id}"
            for label, value in details.items():
                st.caption(f"{label}: `{value}`")

        if not show_actions:
            return

        approve_col, dismiss_col = st.columns(2)
        with approve_col:
            if on_approve is not None and st.button(
                "✅ Approve for calendar",
                key=f"{key_prefix}_approve_{commitment.id}",
                use_container_width=True,
            ):
                on_approve(commitment.id)
        with dismiss_col:
            if on_dismiss is not None and st.button(
                "🗑️ Dismiss",
                key=f"{key_prefix}_dismiss_{commitment.id}",
                use_container_width=True,
            ):
                on_dismiss(commitment.id)
