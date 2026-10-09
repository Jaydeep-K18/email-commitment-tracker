"""Local FastAPI server that publishes the calendar subscription feed.

Serves two public endpoints (PROJECT_PLAN.md Phase 4):

``GET /calendar.ics``  the subscription feed the user's calendar app polls
``GET /health``        liveness check

and, from Phase 10, a token-guarded ``/api/*`` surface used by the Gmail side
panel — see :mod:`src.server.api` and :mod:`src.server.api_token`.

The feed is regenerated from the database on every request, so a calendar app
polling the URL always sees current data without anything needing to push to it.

Binds to ``127.0.0.1`` by default, which means the feed is reachable only from
this machine — no email-derived data is exposed to the network.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from src import config
from src.server import api, api_token, internal_api
from src.server.acting_user import ActAsUser
from src.storage.database import init_db, session_scope
from src.sync.ics_builder import render_ics
from src.sync.sync_engine import calendar_commitments, review_queue

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
    version="0.10.0",
    lifespan=lifespan,
)

# The Gmail panel is a browser extension, so its requests are cross-origin and
# need CORS. Restricted to extension schemes rather than "*": the API is
# reachable by any page the user has open, and while the token is the real
# guard, there is no reason to let arbitrary websites past the browser's own
# check as well. `chrome-extension://*` cannot be expressed in allow_origins,
# hence the regex.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension|moz-extension|safari-web-extension)://.+$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", api_token.TOKEN_HEADER],
)

app.add_middleware(ActAsUser)
app.include_router(api.router)
app.include_router(internal_api.router)


@app.get("/health")
def health() -> dict:
    """Report liveness, feed size, and anything waiting on the user."""
    with session_scope() as session:
        event_count = len(calendar_commitments(session))
        awaiting = len(review_queue(session))
    return {
        "status": "ok",
        "calendar_events": event_count,
        "awaiting_approval": awaiting,
        "calendar_url": (
            f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/calendar.ics"
        ),
    }


@app.get("/calendar.ics")
def calendar_feed() -> Response:
    """Serve the current calendar as a subscribable ``.ics`` feed.

    Selection goes through the sync engine rather than a raw query, so the feed
    a subscriber polls always reflects the same tier policy as a published file.
    """
    with session_scope() as session:
        commitments = calendar_commitments(session)
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
