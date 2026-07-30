"""Local FastAPI server that publishes the calendar subscription feed.

Serves exactly two endpoints (PROJECT_PLAN.md Phase 4):

``GET /calendar.ics``  the subscription feed the user's calendar app polls
``GET /health``        liveness check

The feed is regenerated from the database on every request, so a calendar app
polling the URL always sees current data without anything needing to push to it.

Binds to ``127.0.0.1`` by default, which means the feed is reachable only from
this machine — no email-derived data is exposed to the network.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from src import config
from src.storage.database import commitments_for_calendar, init_db, session_scope
from src.sync.ics_builder import render_ics

log = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    log.info(
        "Calendar feed: http://%s:%s/calendar.ics",
        config.SERVER_HOST, config.SERVER_PORT,
    )
    yield


app = FastAPI(
    title="Email Commitment Tracker",
    description="Serves a local .ics calendar of commitments extracted from email.",
    version="0.4.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    """Report liveness and how many events the feed currently contains."""
    with session_scope() as session:
        event_count = len(commitments_for_calendar(session))
    return {
        "status": "ok",
        "calendar_events": event_count,
        "calendar_url": (
            f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/calendar.ics"
        ),
    }


@app.get("/calendar.ics")
def calendar_feed() -> Response:
    """Serve the current calendar as a subscribable ``.ics`` feed."""
    with session_scope() as session:
        commitments = commitments_for_calendar(session)
        content = render_ics(commitments)

    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'inline; filename="calendar.ics"',
            # Subscribers should always re-read rather than serve a stale copy.
            "Cache-Control": "no-cache, must-revalidate",
        },
    )


def _main() -> int:
    """Run the server:  python -m src.server.calendar_server"""
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    url = f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/calendar.ics"
    # Plain ASCII: the Windows console codepage mangles non-ASCII punctuation.
    print("Email Commitment Tracker - calendar server")
    print(f"  Subscribe your calendar app to:  {url}")
    print(f"  Health check:                    "
          f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/health")
    print("  Press Ctrl+C to stop.\n")
    uvicorn.run(
        app, host=config.SERVER_HOST, port=config.SERVER_PORT, log_level="info"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
