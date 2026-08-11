"""Entry point for the packaged application.

One executable plays two roles, chosen by argv:

* with :data:`~desktop.runtime.SERVE_DASHBOARD_FLAG` it *is* the Streamlit
  server, which needs a main thread of its own for signal handling;
* with no arguments it is the tray, and starts the other role as a child.

Run it from a checkout with ``python -m desktop.main``.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from desktop import runtime
from src import config


def ensure_standard_streams() -> None:
    """Give the process real stdout/stderr objects if it has none.

    A windowed build (``console=False``) starts with ``sys.stdout`` and
    ``sys.stderr`` set to None, and any library that inspects them then fails in
    a way that never happens during development. uvicorn is the one that bit
    here — its log formatter calls ``sys.stdout.isatty()`` while building the
    config, so merely *constructing* a server raised AttributeError inside a
    logging handler, surfacing as "Unable to configure formatter 'default'".

    Pointing them at the null device is enough: nothing in a tray app has a
    console to write to anyway, and the real log goes to a file.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


#: Where a packaged build writes its log. A windowed build has no console, so
#: without this a failure during startup is completely invisible — the tray icon
#: simply sits there having started nothing.
LOG_NAME = "tracker.log"


def log_path():
    return config.DATA_DIR / LOG_NAME


def _configure_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_path(), maxBytes=512_000, backupCount=2, encoding="utf-8"
            )
        )
    except OSError:
        # An unwritable data directory is worth surviving: the console handler
        # still works, and the setup screen is what the user needs anyway.
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )


def serve_dashboard() -> int:
    """Run the Streamlit server in this process, on the main thread.

    A frozen build cannot shell out to ``python -m streamlit``: sys.executable
    is the bundled .exe, and there is no interpreter to hand a module name to.
    Streamlit's bootstrap is the supported way in.
    """
    from streamlit.web import bootstrap

    app_path = config.BASE_DIR / "dashboard" / "app.py"
    if not app_path.exists():
        raise SystemExit(f"Dashboard entry point is missing: {app_path}")

    # Streamlit finds .streamlit/config.toml relative to the working directory,
    # and a packaged app is launched from wherever the user happens to be — so
    # every setting that file guarantees is repeated here rather than assumed.
    flags = {
        # Loopback only: the dashboard is never exposed to the network.
        "server.address": runtime.DASHBOARD_HOST,
        "server.port": runtime.DASHBOARD_PORT,
        "server.headless": True,
        # No telemetry (PROJECT_PLAN.md §16).
        "browser.gatherUsageStats": False,
        "global.developmentMode": False,
        # dashboard/styles.py repaints from a known baseline; letting Streamlit
        # follow the OS instead would leave the two disagreeing about the mode.
        "theme.base": "light",
        "theme.primaryColor": "#2e6fdb",
    }
    bootstrap.load_config_options(flag_options=flags)
    bootstrap.run(str(app_path), False, [], flags)
    return 0


def run_tray() -> int:
    from desktop.tray import TrayApp

    TrayApp().run()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # Must precede logging setup: StreamHandler writes to sys.stderr.
    ensure_standard_streams()
    _configure_logging()
    logging.getLogger(__name__).info(
        "Starting (frozen=%s, role=%s, data=%s)",
        config.is_frozen(),
        "dashboard" if runtime.SERVE_DASHBOARD_FLAG in args else "tray",
        config.DATA_DIR,
    )

    if runtime.SERVE_DASHBOARD_FLAG in args:
        return serve_dashboard()
    return run_tray()


if __name__ == "__main__":
    raise SystemExit(main())
