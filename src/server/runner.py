"""Serve the worker's HTTP endpoints from a background thread.

The worker process hosts them itself: the ``.ics`` feed, the Gmail panel's
endpoints, and the internal setup API the Express server calls. Running them in
the same process means the setup actions see exactly the configuration, keyring
and Google token the worker uses.
"""
from __future__ import annotations

import logging
import threading

from src import config

log = logging.getLogger(__name__)


def start_api_server() -> threading.Thread:
    import uvicorn

    from src.server.calendar_server import app

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.SERVER_HOST,
            port=config.SERVER_PORT,
            log_level="warning",
            # The worker configures logging itself; uvicorn's own config would
            # replace it, and its colour formatter probes sys.stdout.isatty().
            log_config=None,
        )
    )
    # Signal handlers can only be installed from the main thread; the worker
    # owns shutdown, and the server thread is a daemon that dies with it.
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    thread = threading.Thread(target=server.run, name="worker-api", daemon=True)
    thread.start()
    log.info("Worker API on http://%s:%s", config.SERVER_HOST, config.SERVER_PORT)
    return thread
