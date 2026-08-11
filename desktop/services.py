"""Supervises the three processes that make up the running application.

    tray process ──┬── calendar server   (thread, uvicorn)
                   ├── scheduler         (thread, APScheduler)
                   └── dashboard         (child process, Streamlit)

The dashboard is a child process rather than a thread because Streamlit's
bootstrap installs signal handlers, and those only work on a main thread. The
calendar server is a thread because uvicorn can be told to skip that step.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import webbrowser

from desktop import runtime
from src import config
from src.collection.scheduler import SchedulerHandle

log = logging.getLogger(__name__)


class CalendarServer:
    """The .ics feed, served on a background thread."""

    def __init__(self) -> None:
        self._server = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.running:
            return
        if runtime.port_is_open(config.SERVER_HOST, config.SERVER_PORT):
            # Something already holds the port — most likely a second copy of
            # the app, or a dev server left running. Don't fight it.
            log.info("Calendar port %d already in use; not starting a second "
                     "server.", config.SERVER_PORT)
            return

        import uvicorn

        from src.server.calendar_server import app

        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=config.SERVER_HOST,
                port=config.SERVER_PORT,
                log_level="warning",
                # Leave logging alone: desktop.main has already configured it to
                # write to a file, and uvicorn's own colourised formatter probes
                # sys.stdout.isatty(), which a windowed build does not have.
                log_config=None,
            )
        )
        # uvicorn registers SIGINT/SIGTERM handlers on start, which raises
        # ValueError off the main thread. The tray owns shutdown instead.
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

        thread = threading.Thread(
            target=server.run, name="calendar-server", daemon=True
        )
        thread.start()
        self._server, self._thread = server, thread
        log.info("Calendar server on %s", runtime.calendar_url())

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._server, self._thread = None, None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


class DashboardProcess:
    """The Streamlit dashboard, as a child of this process."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None

    def start(self) -> None:
        if self.running:
            return
        if runtime.port_is_open(runtime.DASHBOARD_HOST, runtime.DASHBOARD_PORT):
            log.info("Dashboard port already in use; reusing it.")
            return

        creationflags = 0
        if sys.platform == "win32":
            # Without this a console window flashes up behind the tray icon
            # every time the dashboard starts.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        self._process = subprocess.Popen(
            runtime.self_command(runtime.SERVE_DASHBOARD_FLAG),
            creationflags=creationflags,
        )
        log.info("Dashboard starting at %s", runtime.dashboard_url())

    def stop(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
        self._process = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None


class Supervisor:
    """Owns every moving part, so the tray only has to say start/stop."""

    def __init__(self, notify=None) -> None:
        self.calendar = CalendarServer()
        self.dashboard = DashboardProcess()
        self.scheduler = SchedulerHandle()
        self.notifier = None
        if notify is not None:
            from desktop.notifier import CommitmentNotifier

            self.notifier = CommitmentNotifier(notify)
            self.scheduler.on_cycle = lambda _result: self.notifier.poll()

    # -- lifecycle ---------------------------------------------------------

    def start(self, *, run_scheduler: bool = True) -> None:
        from src.storage.database import init_db

        init_db()
        if self.notifier is not None:
            # Record the existing backlog before any cycle runs, so the first
            # notification describes new arrivals rather than the whole history.
            self.notifier.prime()
        self.calendar.start()
        self.dashboard.start()
        if run_scheduler:
            self.scheduler.start()

    def stop(self) -> None:
        self.scheduler.shutdown()
        self.dashboard.stop()
        self.calendar.stop()

    # -- actions -----------------------------------------------------------

    def open_dashboard(self) -> None:
        """Bring the dashboard up in a browser, starting it if it died."""
        self.dashboard.start()
        # Cold-starting Streamlit from a bundle takes a few seconds; opening the
        # browser first would just show a connection error.
        runtime.wait_for_port(runtime.DASHBOARD_HOST, runtime.DASHBOARD_PORT)
        webbrowser.open(runtime.dashboard_url())

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.scheduler.shutdown()
        else:
            self.scheduler.start()

    @property
    def paused(self) -> bool:
        return not self.scheduler.running
