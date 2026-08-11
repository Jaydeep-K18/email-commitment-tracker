"""The system tray presence.

Everything the user can do without opening the dashboard lives here: open it,
force a check now, pause the background schedule, and quit. The tray is also
what shows desktop notifications, since on Windows a balloon has to be attached
to a tray icon that already exists.
"""
from __future__ import annotations

import logging
import threading

import pystray

from desktop import runtime
from desktop.icon import tray_image
from desktop.services import Supervisor

log = logging.getLogger(__name__)

APP_TITLE = "Email Commitment Tracker"


class TrayApp:
    """Owns the icon and the supervisor beneath it."""

    def __init__(self) -> None:
        self.icon = pystray.Icon(
            "email_commitment_tracker",
            icon=tray_image(),
            title=APP_TITLE,
        )
        self.supervisor = Supervisor(notify=self.notify)
        self.icon.menu = self._menu()

    # -- notifications -----------------------------------------------------

    def notify(self, title: str, message: str) -> None:
        """Show a desktop notification, if this platform's backend can.

        pystray reports the capability rather than raising, and a missing
        notification is never worth interrupting a background cycle for.
        """
        if not getattr(self.icon, "HAS_NOTIFICATION", False):
            log.info("Notification (unsupported here): %s — %s", title, message)
            return
        try:
            self.icon.notify(message, title)
        except Exception:  # noqa: BLE001
            log.debug("Notification failed.", exc_info=True)

    # -- menu --------------------------------------------------------------

    def _menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "Open dashboard",
                self._open_dashboard,
                # Makes a double-click on the icon open the dashboard.
                default=True,
            ),
            pystray.MenuItem("Check email now", self._check_now),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Pause background updates",
                self._toggle_pause,
                checked=lambda _item: self.supervisor.paused,
            ),
            pystray.MenuItem("Setup and settings", self._open_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )

    # -- actions -----------------------------------------------------------
    # Each runs on a thread: pystray dispatches menu callbacks on the UI thread,
    # and anything slow there freezes the menu (and on Windows, the tray).

    def _in_background(self, target, name: str) -> None:
        threading.Thread(target=target, name=name, daemon=True).start()

    def _open_dashboard(self, *_args) -> None:
        self._in_background(self.supervisor.open_dashboard, "open-dashboard")

    def _open_settings(self, *_args) -> None:
        def go() -> None:
            self.supervisor.open_dashboard()

        self._in_background(go, "open-settings")

    def _check_now(self, *_args) -> None:
        def run() -> None:
            result = self.supervisor.scheduler.run_now()
            if result is not None and not result.ok:
                failed = ", ".join(stage for stage, _ in result.errors)
                self.notify(APP_TITLE, f"Check finished with problems: {failed}")

        self._in_background(run, "check-now")

    def _toggle_pause(self, *_args) -> None:
        self.supervisor.set_paused(not self.supervisor.paused)

    def _quit(self, *_args) -> None:
        self.supervisor.stop()
        self.icon.stop()

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> None:
        """Start the services, then hand the main thread to the tray loop."""

        def on_ready(icon: pystray.Icon) -> None:
            icon.visible = True
            log.info("Tray ready; starting services.")
            try:
                self.supervisor.start()
            except Exception:
                # pystray runs this on its own thread and swallows what escapes,
                # which in a windowed build means an icon that started nothing
                # and explained nothing.
                log.exception("Could not start the background services.")
                self.notify(
                    APP_TITLE,
                    "Could not start. See tracker.log in the app data folder.",
                )
                return
            self.notify(
                APP_TITLE,
                "Running in the background.\n"
                f"Calendar feed: {runtime.calendar_url()}",
            )

        # setup= runs on pystray's own thread once the icon exists, which is the
        # only point at which notifications can be shown.
        self.icon.run(setup=on_ready)
