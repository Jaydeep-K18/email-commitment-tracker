"""Run each request to the worker's HTTP API as the account it is for.

- ``/internal/*`` comes from the Express server, which names the signed-in
  user in ``X-User-Id`` (the internal token, checked by the routes, is what
  makes that header trustworthy).
- ``/api/*`` (the Gmail panel) and ``/calendar.ics`` act for the account whose
  mailbox this machine reads, until each account has its own.

A pure ASGI middleware rather than a dependency, so the user is set in the
request's own context and every sync route, run in a worker thread with a copy
of that context, sees it.
"""
from __future__ import annotations

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from src.storage.accounts import mailbox_owner_id
from src.storage.database import session_scope
from src.storage.models import User
from src.storage.tenancy import acting_as

USER_HEADER = b"x-user-id"


def _active_user(raw: bytes | None) -> int | None:
    if not raw or not raw.isdigit():
        return None
    with acting_as(None), session_scope() as session:
        return session.scalar(select(User.id).where(User.id == int(raw), User.disabled_at.is_(None)))


def _mailbox_owner() -> int | None:
    with acting_as(None), session_scope() as session:
        return mailbox_owner_id(session)


class ActAsUser:
    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if path.startswith("/internal"):
            user_id = await run_in_threadpool(_active_user, dict(scope["headers"]).get(USER_HEADER))
        elif path.startswith("/api") or path == "/calendar.ics":
            user_id = await run_in_threadpool(_mailbox_owner)
        else:
            user_id = None
        with acting_as(user_id):
            await self.app(scope, receive, send)
