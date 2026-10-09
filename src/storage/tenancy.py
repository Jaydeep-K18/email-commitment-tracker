"""Whose data a piece of work is about.

Every row of user data carries a ``user_id``. Work done for one user (a job, or
a request the Node server forwards) runs inside ``acting_as(user_id)``, and
while it does:

- every ORM query and bulk UPDATE/DELETE is limited to that user's rows;
- new rows are stamped with that user, and a row naming anyone else is refused;
- on Postgres each transaction also runs as the ``commitmail_tenant`` role with
  ``app.user_id`` set, so row-level security (migration 0005) enforces the same
  thing in the database, beneath this code.

Outside ``acting_as`` is the system context: the dispatcher, the outbox relay
and the scheduler, which must see across users. On Postgres the system
connection is the only one allowed past row-level security.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import event, text
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from src.storage.models import (
    CalendarFlag,
    Commitment,
    EmailTag,
    Event,
    Job,
    JobAttempt,
    Notification,
    RawEmail,
    SavedView,
    SentMessage,
    Setting,
    SyncLog,
    Tag,
    VipContact,
)

TENANT_ROLE = "commitmail_tenant"

TENANT_MODELS = (
    RawEmail, VipContact, Commitment, SyncLog, Tag, EmailTag, SavedView, SentMessage,
    Event, Job, JobAttempt, Notification, Setting, CalendarFlag,
)

_current: ContextVar[int | None] = ContextVar("commitmail_user", default=None)


class TenancyError(PermissionError):
    """Code acting for one user tried to write another user's row."""


def current_user_id() -> int | None:
    return _current.get()


@contextmanager
def acting_as(user_id: int | None) -> Iterator[None]:
    """Run the enclosed work for ``user_id`` (None: the system context)."""
    token = _current.set(user_id)
    try:
        yield
    finally:
        _current.reset(token)


@event.listens_for(Session, "do_orm_execute")
def _only_this_users_rows(state: ORMExecuteState) -> None:
    user_id = _current.get()
    if user_id is None or state.is_column_load or state.is_relationship_load:
        return
    if state.is_select or state.is_update or state.is_delete:
        state.statement = state.statement.options(
            *(
                with_loader_criteria(model, model.user_id == user_id, include_aliases=True)
                for model in TENANT_MODELS
            )
        )


@event.listens_for(Session, "before_flush")
def _stamp_new_rows(session: Session, flush_context, instances) -> None:  # noqa: ANN001
    user_id = _current.get()
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, TENANT_MODELS):
            continue
        if obj.user_id is None:
            obj.user_id = user_id
        elif user_id is not None and obj.user_id != user_id:
            raise TenancyError(
                f"{type(obj).__name__} belongs to user {obj.user_id}, not {user_id}"
            )


@event.listens_for(Session, "after_begin")
def _scope_the_transaction(session: Session, transaction, connection) -> None:  # noqa: ANN001
    user_id = _current.get()
    if user_id is None or connection.dialect.name != "postgresql":
        return
    connection.exec_driver_sql(f"SET LOCAL ROLE {TENANT_ROLE}")
    connection.execute(text("SELECT set_config('app.user_id', :id, true)"), {"id": str(int(user_id))})
