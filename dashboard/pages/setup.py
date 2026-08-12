"""First-run setup — the only page shown until the app can reach a mailbox.

Two things are needed: which mailbox to read, and permission to read it. The
password goes straight into the OS keyring; this page never writes it to disk
and never shows it back.
"""
from __future__ import annotations

import streamlit as st

from dashboard import styles
from src import config, first_run
from src.auth import google_auth
from src.server import api_token

st.title("Welcome — let's connect your mailbox")
st.caption(
    "Your email is read on this machine and the deadlines are extracted by a "
    "local model — no email content is sent to any AI service."
)

state = first_run.setup_state()

# --- The easy path --------------------------------------------------------
with styles.panel(
    "Connect with Google", icon="🔗", key="ect-panel-setup-google",
    caption="Reads your mail and writes your calendar events. Recommended.",
):
    account = google_auth.account()
    if account is not None:
        st.success(f"Signed in{f' as {account.email}' if account.email else ''}.")
        st.caption(
            f"Mail access: {'yes' if account.has_mail else 'no'} · "
            f"Calendar access: {'yes' if account.has_calendar else 'no'}"
        )
        if st.button("Disconnect Google", use_container_width=True):
            google_auth.clear_token()
            st.rerun()
    elif not google_auth.client_secrets_present():
        st.warning(
            "No Google client configuration found yet. Create an OAuth client "
            "ID of type **Desktop app** in your own Google Cloud project "
            "(free), download the JSON, and save it as:"
        )
        st.code(str(config.GOOGLE_CLIENT_SECRETS), language=None)
        st.caption("Step-by-step walkthrough: `docs/google-setup.md`")
    else:
        st.markdown(
            "This is what lets deadlines appear in **Google Calendar on your "
            "phone**. The local `.ics` feed cannot do that: Google fetches "
            "subscription URLs from its own servers, which cannot reach this "
            "machine."
        )
        if st.button("Sign in with Google", type="primary", use_container_width=True):
            with st.spinner("Finish signing in from the browser tab that opened…"):
                try:
                    signed = google_auth.sign_in()
                except Exception as exc:  # noqa: BLE001 - shown to the user
                    st.error(f"Sign-in did not complete: {exc}")
                else:
                    st.success(f"Signed in as {signed.email or 'your account'}.")
                    st.rerun()

st.divider()
st.caption(
    "Or connect any other mailbox (Outlook, Yahoo, university IMAP) with an "
    "app password:"
)

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
    if state.signed_in_with_google:
        st.success(
            "Nothing to do — events are written straight into your Google "
            "Calendar, and appear on your phone within a minute."
        )
    else:
        st.markdown(
            "Add this as a **subscribed calendar** (not an import) so it keeps "
            "itself up to date:"
        )
        st.code(
            f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/calendar.ics",
            language=None,
        )
        st.caption(
            "Works with **Outlook desktop**, **Apple Calendar** and "
            "**Thunderbird**, which fetch the feed from this machine."
        )
        st.warning(
            "This will **not** work with Google Calendar. Google fetches "
            "subscription URLs from its own servers, and they cannot reach "
            "`127.0.0.1` on your laptop. Connect with Google above instead."
        )

st.divider()

with styles.panel(
    "Gmail side panel", icon="🧩", key="ect-panel-setup-ext",
    caption="Optional. Adds an 'Add to calendar' panel inside Gmail.",
):
    st.markdown(
        "1. Open `chrome://extensions`, turn on **Developer mode**\n"
        "2. **Load unpacked**, and choose the `extension` folder in this project\n"
        "3. Open the extension's **options** and paste the token below"
    )

    if st.session_state.pop("ect_token_rotated", False):
        st.warning("Old token revoked — paste the new one into the extension.")

    if st.session_state.get("ect_show_token"):
        st.code(api_token.get_or_create_token(), language=None)
        st.caption(
            "Treat this like a password. It is what stops other websites you "
            "visit from talking to the tracker — `127.0.0.1` is reachable by "
            "any page your browser has open."
        )
    # Not shown until asked for: the dashboard is the sort of thing that ends
    # up on a shared screen.
    elif st.button("Show access token", use_container_width=True):
        st.session_state["ect_show_token"] = True
        st.rerun()

    if st.button("Generate a new token", use_container_width=True):
        api_token.rotate_token()
        st.session_state["ect_show_token"] = True
        st.session_state["ect_token_rotated"] = True
        st.rerun()
