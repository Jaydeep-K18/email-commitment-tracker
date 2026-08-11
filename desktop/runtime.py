"""Process and network plumbing shared by the tray and the services it starts.

The awkward part of packaging this app is that ``sys.executable`` stops being a
Python interpreter once PyInstaller has bundled it: it becomes the application's
own .exe. Anything that used to shell out to ``python -m streamlit`` therefore
has to re-launch *this* program with a flag instead, which is what
:func:`self_command` is for.
"""
from __future__ import annotations

import socket
import sys
import time

from src import config

#: Argument that makes :mod:`desktop.main` serve the dashboard instead of
#: showing a tray icon. Streamlit installs signal handlers, which only work on a
#: main thread, so the dashboard cannot simply be a thread of the tray process.
SERVE_DASHBOARD_FLAG = "--serve-dashboard"

#: Port the dashboard listens on. Fixed rather than random so the URL in the
#: setup screen, the tray menu and any bookmark the user makes all agree.
DASHBOARD_PORT = 8501
DASHBOARD_HOST = "127.0.0.1"


def self_command(*args: str) -> list[str]:
    """The command that re-launches this application.

    Frozen, ``sys.executable`` is the bundled .exe and the arguments go straight
    to it. From a checkout it is a Python interpreter, which needs to be pointed
    at the module.
    """
    if config.is_frozen():
        return [sys.executable, *args]
    return [sys.executable, "-m", "desktop.main", *args]


def dashboard_url() -> str:
    return f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"


def calendar_url() -> str:
    return f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/calendar.ics"


def port_is_open(host: str, port: int, timeout: float = 0.35) -> bool:
    """Whether something is already accepting connections there."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def wait_for_port(
    host: str, port: int, timeout: float = 45.0, interval: float = 0.25
) -> bool:
    """Block until a port accepts connections, or the timeout expires.

    Used before opening a browser: a packaged build starts Streamlit cold, which
    takes a few seconds, and pointing a browser at it too early shows a
    connection error the user has to refresh past.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_is_open(host, port):
            return True
        time.sleep(interval)
    return False
