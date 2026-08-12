"""Streamlit entry point for the Email Commitment Tracker dashboard.

Run it with::

    streamlit run dashboard/app.py

Everything renders from the local SQLite database — the dashboard reads storage
directly rather than going through the API, which PROJECT_PLAN.md §11 allows and
which keeps the moving parts down.

Streamlit re-executes this script on every interaction, so anything that must
exist exactly once (the background scheduler, the database schema) is created
through ``st.cache_resource``, which is evaluated once per server process rather
than once per rerun.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Streamlit puts this file's own directory on sys.path, not the project root, so
# ``import src...`` would fail. Fix that before importing anything from src —
# the page modules loaded below inherit this.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st  # noqa: E402

from dashboard import notifications, styles  # noqa: E402
from src import config, first_run  # noqa: E402
from src.collection.scheduler import SchedulerHandle  # noqa: E402
from src.storage.database import init_db  # noqa: E402

st.set_page_config(
    page_title="Email Commitment Tracker",
    page_icon="📬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Palette, stylesheet and cursor spotlight. Must run before anything renders so
# no element is ever painted with Streamlit's default chrome first.
styles.inject()


@st.cache_resource
def _database() -> bool:
    """Create/migrate the schema once per server process."""
    init_db()
    return True


@st.cache_resource
def _scheduler() -> SchedulerHandle:
    """One scheduler per server process, shared across reruns and sessions."""
    return SchedulerHandle()


_database()
scheduler = _scheduler()

# Until there is a mailbox to read, every other page would show an empty state
# and the scheduler controls would fail on the first cycle. Setup replaces the
# whole dashboard rather than sitting alongside it as a page nobody finds.
setup_needed = first_run.needs_setup()

# Read once per rerun and pass the same notice to both the badge and the toast,
# so the two can never disagree about what has arrived.
notice = notifications.pending_notice()


def _sidebar() -> None:
    with st.sidebar:
        st.title("📬 Tracker")
        styles.theme_toggle()
        st.caption("Private by design — email is processed on this machine only.")

        if setup_needed:
            # Nothing below this point can work yet, and offering a Fetch button
            # that is guaranteed to fail is worse than offering nothing.
            st.info(f"Setup needed: {first_run.setup_state().missing}.")
            return

        notifications.render_sidebar_badge(notice)

        st.divider()
        st.subheader("Automatic updates")

        running = scheduler.running
        st.markdown(
            f"Status: {'🟢 running' if running else '⚪ stopped'}  \n"
            f"Every {scheduler.interval_minutes} minutes"
        )

        if running:
            next_run = scheduler.next_run_at
            if next_run:
                st.caption(f"Next run: {next_run.strftime('%H:%M:%S')}")
            if st.button("Stop", use_container_width=True):
                scheduler.shutdown()
                st.rerun()
        else:
            if st.button("Start", type="primary", use_container_width=True):
                scheduler.start()
                st.rerun()

        if st.button("⟳ Fetch now", use_container_width=True):
            with st.spinner("Fetching, extracting, syncing… this can take a while."):
                result = scheduler.run_now()
            if result.ok:
                st.success(result.summary())
            else:
                # A cycle can partly succeed — show what worked and what did not.
                st.warning(result.summary())
                for stage, message in result.errors:
                    st.caption(f"{stage}: {message}")

        last = scheduler.last_result
        if last is not None:
            st.caption(
                f"Last run {last.started_at.strftime('%d %b %H:%M')} — "
                f"{last.summary()}"
            )

        st.divider()
        if first_run.setup_state().signed_in_with_google:
            st.caption("Calendar")
            st.success("Writing to Google Calendar", icon="📅")
        else:
            st.caption("Calendar subscription URL")
            st.code(
                f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/calendar.ics",
                language=None,
            )
            # Deliberately names the calendar apps this can work with. Google
            # fetches subscription URLs from its own servers, which cannot reach
            # a loopback address here, so pointing Google Calendar at this is a
            # dead end no amount of retrying fixes.
            st.caption(
                "Serve it with `python -m src.server.calendar_server`, then "
                "subscribe from Outlook desktop, Apple Calendar or Thunderbird. "
                "For **Google Calendar**, connect Google on the setup page."
            )


_sidebar()

if setup_needed:
    # Sole page, so there is no navigation to wander off into mid-setup.
    st.navigation([
        st.Page("pages/setup.py", title="Setup", icon="👋", default=True),
    ]).run()
else:
    notifications.render_toast(notice)
    st.navigation([
        st.Page("pages/overview.py", title="Overview", icon="📊", default=True),
        st.Page("pages/feed.py", title="Commitments", icon="📋"),
        st.Page("pages/review_queue.py", title="Review queue", icon="✅"),
        st.Page("pages/graph.py", title="Network", icon="🕸️"),
        st.Page("pages/vip_manager.py", title="VIP contacts", icon="👥"),
        # Reachable after setup too, so credentials can be changed and the
        # calendar URL found again without hunting for a terminal.
        st.Page("pages/setup.py", title="Mailbox setup", icon="⚙️"),
    ]).run()
