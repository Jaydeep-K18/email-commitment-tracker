"""First-run setup — the only page shown until the app can reach a mailbox.

Two things are needed: which mailbox to read, and permission to read it. The
password goes straight into the OS keyring; this page never writes it to disk
and never shows it back.
"""
from __future__ import annotations

import streamlit as st

from dashboard import styles
from src import config, first_run

st.title("Welcome — let's connect your mailbox")
st.caption(
    "Everything stays on this machine. Your email is read locally, the "
    "deadlines are extracted by a local model, and nothing is uploaded."
)

state = first_run.setup_state()

with styles.panel("Step 1 — your email address", icon="📮", key="ect-panel-setup-user"):
    address = st.text_input(
        "Email address",
        value=config.IMAP_USER,
        placeholder="you@gmail.com",
        help="The mailbox the tracker will read, over IMAP, read-only.",
    )

with styles.panel("Step 2 — an app password", icon="🔑", key="ect-panel-setup-pass"):
    st.markdown(
        "Gmail does not allow apps to use your normal password. Create a "
        "**16-character App Password** instead:"
    )
    st.markdown(
        "1. Turn on 2-Step Verification in your Google Account\n"
        "2. Go to **Google Account → Security → App passwords**\n"
        "3. Create one for “Mail”, and paste the 16 characters below"
    )
    password = st.text_input(
        "App password",
        type="password",
        placeholder="xxxx xxxx xxxx xxxx",
        help="Stored in the Windows Credential Manager, never in a file.",
    )
    st.caption(
        "Stored in your operating system's credential store. It is never "
        "written to a file in this project and never leaves this machine."
    )

test_col, save_col = st.columns(2)

with test_col:
    if st.button("Test connection", use_container_width=True):
        if not address or not password:
            st.warning("Enter both an address and an app password first.")
        else:
            with st.spinner(f"Connecting to {config.IMAP_HOST}…"):
                ok, message = first_run.check_connection(address, password)
            st.success(message) if ok else st.error(message)

with save_col:
    if st.button("Save and start", type="primary", use_container_width=True):
        if not address or not password:
            st.warning("Enter both an address and an app password first.")
        else:
            with st.spinner("Checking those details…"):
                ok, message = first_run.check_connection(address, password)
            if not ok:
                st.error(message)
            else:
                first_run.save_email_address(address)
                first_run.save_password(address, password)
                st.success("Saved. Starting the tracker…")
                st.rerun()

st.divider()

with styles.panel(
    "Step 3 — subscribe your calendar", icon="📅", key="ect-panel-setup-cal"
):
    st.markdown(
        "Once commitments start arriving they are published to this address. "
        "Add it as a **subscribed calendar** (not an import) so it keeps "
        "itself up to date:"
    )
    st.code(
        f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/calendar.ics",
        language=None,
    )
    st.caption(
        "Google Calendar: Other calendars → From URL. "
        "Outlook: Add calendar → Subscribe from web. "
        "Apple Calendar: File → New Calendar Subscription."
    )
    if not state.complete:
        st.caption(
            "This link only answers while the tracker is running on this "
            "machine, which is what keeps the feed private."
        )
