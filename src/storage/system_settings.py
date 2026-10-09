"""Small facts about a user's installation, stored in their ``settings``."""
from __future__ import annotations

import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.storage.models import Setting
from src.storage.user_settings import key

SYSTEM_SECTION = "system"


def install_id(session: Session) -> str:
    """A random id for this installation, created on first use and then fixed.

    It salts the Google Calendar event ids the app chooses. Without it, a fresh
    database would number its commitments from 1 again and derive the *same*
    event ids as the old one, so its first sync would overwrite events that
    belong to commitments the new database has never heard of. It lives in the
    database rather than a file so that migrating the data carries it along.
    """
    row = session.get(Setting, key(SYSTEM_SECTION))
    if row is not None and row.value.get("install_id"):
        return row.value["install_id"]

    new_id = secrets.token_hex(8)
    try:
        with session.begin_nested():   # a savepoint: losing a race is recoverable
            if row is None:
                session.add(Setting(section=SYSTEM_SECTION, value={"install_id": new_id}))
            else:
                row.value = {**row.value, "install_id": new_id}
            session.flush()
    except IntegrityError:
        # Another process created the row first; theirs wins, so read it back.
        session.expire_all()
        return session.get(Setting, key(SYSTEM_SECTION)).value["install_id"]
    return new_id
