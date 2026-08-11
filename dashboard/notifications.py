"""Streamlit rendering for "new email from a VIP" notifications (Phase 6).

Nothing here is ever *pushed*. The fetch cycle runs on an APScheduler background
thread (:mod:`src.collection.scheduler`) and Streamlit's API belongs to the
script-run thread, so a scheduler that called ``st.toast`` would at best have the
call dropped. Instead the cycle only writes ``raw_emails.notification_seen``, and
the dashboard *derives* the toast and the badge from that state on its next
rerun. The side benefit is durability: an arrival that happened while the browser
tab was closed is still waiting when the user comes back, which a pushed toast
could never manage.

The queries and the wording live in :mod:`src.storage.database` and
:mod:`dashboard.data` so they stay unit-testable without a browser. What is left
here is rendering, plus the per-browser-session bookkeeping that stops a single
arrival being toasted again on every rerun.

Wiring (in ``dashboard/app.py``)::

    from dashboard import notifications

    notice = notifications.pending_notice()
    notifications.render_toast(notice)          # once per rerun, any container
    with st.sidebar:
        notifications.render_sidebar_badge(notice)
"""
from __future__ import annotations

import streamlit as st

from dashboard.data import NewEmailNotice, new_email_notice
from src.storage.database import mark_emails_seen, session_scope

#: Ids already announced in *this* browser session. Streamlit re-executes the
#: whole script on every widget interaction, so a toast driven purely by
#: database state would re-fire on every click until the user dismissed it.
_TOASTED_IDS_KEY = "_notified_vip_email_ids"


def pending_notice() -> NewEmailNotice:
    """Read the current unseen-VIP-email state from the database."""
    with session_scope() as session:
        return new_email_notice(session)


def _announced_ids() -> set[int]:
    if _TOASTED_IDS_KEY not in st.session_state:
        st.session_state[_TOASTED_IDS_KEY] = set()
    return st.session_state[_TOASTED_IDS_KEY]


def _forget_announced() -> None:
    if _TOASTED_IDS_KEY in st.session_state:
        del st.session_state[_TOASTED_IDS_KEY]


def render_toast(notice: NewEmailNotice | None = None) -> bool:
    """Toast unseen arrivals; returns whether anything was actually shown.

    Fires only when at least one id has not been announced in this session, but
    then announces the *whole* pending set rather than only the new ids — so the
    toast and the sidebar badge can never disagree about how many emails are
    waiting.
    """
    notice = pending_notice() if notice is None else notice
    if not notice:
        return False

    announced = _announced_ids()
    if all(email_id in announced for email_id in notice.email_ids):
        return False

    announced.update(notice.email_ids)
    st.toast(notice.message, duration="long")
    return True


def render_sidebar_badge(
    notice: NewEmailNotice | None = None,
    *,
    dismissable: bool = True,
    key: str = "vip_email_mark_seen",
) -> NewEmailNotice:
    """Render the unread count into the *current* container.

    Deliberately does not open ``st.sidebar`` itself, so the caller decides
    placement; call it inside the sidebar's ``with`` block. Renders nothing when
    there is nothing waiting — an empty badge is just noise.
    """
    notice = pending_notice() if notice is None else notice
    if not notice:
        return notice

    noun = "new email" if notice.count == 1 else "new emails"
    st.badge(
        f"{notice.count} {noun}",
        icon=":material/mark_email_unread:",
        color="red",
    )
    summary = notice.sender_summary
    if summary:
        st.caption(f"From {summary}")

    if dismissable and st.button(
        "Mark as read", key=key, use_container_width=True
    ):
        mark_all_seen()
        st.rerun()
    return notice


def mark_all_seen() -> int:
    """Mark every unseen VIP email as seen. Returns how many rows changed.

    Idempotent — a double-click (which Streamlit reruns make easy) finds nothing
    left unseen and reports 0.
    """
    with session_scope() as session:
        changed = mark_emails_seen(session)
    # Nothing is pending any more, so the announced-id memory can be dropped
    # rather than growing for the life of the browser session.
    _forget_announced()
    return changed


def mark_seen(notice: NewEmailNotice) -> int:
    """Mark only the emails a specific notice covered.

    Preferred over :func:`mark_all_seen` when acting on something the user was
    actually shown: anything that arrived after the notice was rendered stays
    unseen and gets its own announcement.
    """
    with session_scope() as session:
        changed = mark_emails_seen(session, notice.email_ids)
    _forget_announced()
    return changed
