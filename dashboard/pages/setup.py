"""First-run setup — the only page shown until the app can actually work.

Four ordered steps, because someone who has just installed this has no idea what
it needs and should never have to guess what comes next. Each reports done or
not-done on its own, so a half-finished setup is visible rather than silent.

The first three are genuine requirements. Step 4 always has a working default,
so it can never block anyone: the ``.ics`` file is written regardless, and the
app is deliberately not locked to any one calendar vendor.

The app password goes straight into the OS keyring; this page never writes it to
disk and never shows it back.
"""
from __future__ import annotations

import streamlit as st

from dashboard import styles
from src import config, first_run
from src.auth import google_auth, google_client
from src.server import api_token

st.title("Welcome — let's get you set up")
st.caption(
    "Your email is read on this machine and the deadlines are extracted by a "
    "local model — no email content is sent to any AI service."
)

state = first_run.setup_state()
account = google_auth.account()


def tick(done: bool) -> str:
    return "✅" if done else "⬜"


# --- Step 1 — the local model ---------------------------------------------

with styles.panel(
    f"{tick(state.model_ready)} Step 1 — the local model",
    icon="🧠", key="ect-panel-setup-ollama",
    caption="Ollama runs the AI on your own machine. Nothing is sent to a server.",
):
    if state.model_ready:
        st.success(f"Ready — running `{config.OLLAMA_MODEL}` locally.")
    else:
        if not state.ollama_running:
            st.warning("Ollama is not running on this machine yet.")
            st.markdown(
                "1. Download and install it from [ollama.com](https://ollama.com/download)\n"
                "2. Open it — it runs quietly in the background\n"
                "3. Then pull the model with this command:"
            )
        else:
            st.warning(
                f"Ollama is running, but the `{config.OLLAMA_MODEL}` model is "
                "not installed yet. Pull it with:"
            )
        st.code(f"ollama pull {config.OLLAMA_MODEL}", language="bash")
        st.caption(
            "About 2 GB, downloaded once. This is what reads your email, which "
            "is why it lives on your machine rather than in a cloud service."
        )
        if st.button("Check again", use_container_width=True):
            st.rerun()

st.divider()

# --- Step 2 — sign in ------------------------------------------------------

with styles.panel(
    f"{tick(state.signed_in_with_google)} Step 2 — sign in with Google",
    icon="🔗", key="ect-panel-setup-google",
    caption="Identifies you. Does not read your mail and does not touch your calendar.",
):
    if account is not None:
        st.success(f"Signed in{f' as {account.email}' if account.email else ''}.")
        if st.button("Disconnect Google", use_container_width=True):
            google_auth.clear_token()
            st.rerun()
    elif not google_auth.client_secrets_present():
        st.warning(
            "This build has no Google client configured. Create an OAuth client "
            "ID of type **Desktop app** in your own Google Cloud project (free), "
            "download the JSON, and save it as:"
        )
        st.code(str(config.GOOGLE_CLIENT_SECRETS), language=None)
        st.caption("Step-by-step walkthrough: `docs/google-setup.md`")
    else:
        st.markdown(
            "This asks only for your **email address** — nothing else. "
            "Calendar access is requested separately in step 4, and only if you "
            "choose Google Calendar."
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

# --- Step 3 — the mailbox --------------------------------------------------

with styles.panel(
    f"{tick(state.mailbox_ready)} Step 3 — connect your mailbox",
    icon="📮", key="ect-panel-setup-mail",
    caption="Read-only. Works with Gmail, Outlook, Yahoo and university IMAP.",
):
    if state.google_mail_access:
        st.success("Reading mail through the Gmail API with your own Google client.")
    else:
        if state.has_user and state.has_password:
            st.success(f"Connected to {config.IMAP_USER}.")

        st.markdown(
            "Mail is read with a **16-character app password**, not your normal "
            "one. This keeps the app out of Google's restricted-permission "
            "review, and it is the only route that works for non-Gmail mailboxes."
        )
        with st.expander("How to create an app password", expanded=not state.mailbox_ready):
            st.markdown(
                "1. Turn on 2-Step Verification in your Google Account\n"
                "2. Go to **Google Account → Security → App passwords**\n"
                "3. Create one for “Mail”, and paste the 16 characters below\n\n"
                "Outlook, Yahoo and most university mail have an equivalent "
                "setting, often called an *app password* or *IMAP password*."
            )

        address = st.text_input(
            "Email address",
            value=config.IMAP_USER,
            placeholder="you@gmail.com",
            help="The mailbox the tracker will read, over IMAP, read-only.",
        )
        password = st.text_input(
            "App password",
            type="password",
            placeholder="xxxx xxxx xxxx xxxx",
            help="Stored in your OS credential store, never in a file.",
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
            if st.button("Save", type="primary", use_container_width=True):
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
                        st.success("Saved.")
                        st.rerun()

st.divider()

# --- Step 4 — where events go ---------------------------------------------

with styles.panel(
    "Step 4 — where should events go?",
    icon="📅", key="ect-panel-setup-cal",
    caption="Optional — the calendar file is always written either way.",
):
    st.markdown(
        "The tracker is not tied to any one calendar app. Pick whichever you "
        "already use:"
    )

    ics_tab, feed_tab, google_tab = st.tabs(
        ["Download a file", "Subscribe (auto-updating)", "Google Calendar"]
    )

    with ics_tab:
        st.markdown(
            "A standard `.ics` file. Double-click it to import into **Windows "
            "Calendar**, **Outlook**, **Apple Calendar** or anything else that "
            "reads calendars."
        )
        if config.ICS_PATH.exists():
            st.download_button(
                "Download calendar file",
                data=config.ICS_PATH.read_bytes(),
                file_name="email-commitments.ics",
                mime="text/calendar",
                use_container_width=True,
            )
            st.caption(
                "A snapshot — re-download after a sync to pick up new deadlines. "
                "Use the subscribe option instead if you want it to keep itself "
                "up to date."
            )
        else:
            st.info("Nothing published yet. Run a sync and this will appear.")

    with feed_tab:
        st.markdown(
            "Add this as a **subscribed calendar** (not an import) and it keeps "
            "itself up to date while the app is running:"
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
            "`127.0.0.1` on your laptop. Use the Google Calendar tab instead."
        )

    with google_tab:
        if account is None:
            st.info("Sign in with Google in step 2 first.")
        elif account.has_calendar and config.CALENDAR_TARGET != config.CALENDAR_TARGET_ICS:
            st.success(
                "Events are written straight into your Google Calendar, and "
                "appear on your phone within a minute."
            )
            if st.button("Stop using Google Calendar", use_container_width=True):
                first_run.save_setting("CALENDAR_TARGET", config.CALENDAR_TARGET_ICS)
                st.rerun()
        else:
            st.markdown(
                "The only option that reaches **your phone** with no extra work. "
                "This asks for one more permission: to create and update events."
            )
            st.caption(
                "Events-only access. The app cannot read or delete your other "
                "calendars."
            )
            if st.button(
                "Connect Google Calendar", type="primary", use_container_width=True
            ):
                with st.spinner("Approve the calendar permission in your browser…"):
                    try:
                        google_auth.grant_calendar_access()
                    except Exception as exc:  # noqa: BLE001 - shown to the user
                        st.error(f"Could not connect the calendar: {exc}")
                    else:
                        first_run.save_setting(
                            "CALENDAR_TARGET", config.CALENDAR_TARGET_GOOGLE
                        )
                        st.rerun()

st.divider()

# --- Optional extras -------------------------------------------------------

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

if config.GOOGLE_CLIENT_SECRETS.exists() and not google_client.is_user_supplied():
    st.caption(
        f"Note: a Google client file exists at {config.GOOGLE_CLIENT_SECRETS} "
        "but could not be read as a Desktop app credential."
    )
