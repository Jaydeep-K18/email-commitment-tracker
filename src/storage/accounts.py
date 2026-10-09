"""Accounts, as the worker needs to know them."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.storage.models import User


def mailbox_owner_id(session: Session) -> int | None:
    """The account whose credentials this machine holds: the first admin.

    Mail and Google Calendar access still come from the OS keyring, which holds
    one person's sign-in. Until each account signs in with Google itself, only
    this account's work may use them — anything else would read one person's
    mailbox into another person's account.
    """
    return session.scalar(
        select(User.id).where(User.is_admin.is_(True), User.disabled_at.is_(None)).order_by(User.id)
    )
