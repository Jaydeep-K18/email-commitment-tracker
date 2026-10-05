"""Pytest bootstrap.

Puts the repo root on sys.path so ``import src`` works, and gives every test its
own throwaway database and calendar file.

The isolation is autouse on purpose. Without it, any code path that opens a
session without a test having swapped the engine first — a settings lookup, a
default ``.ics`` path — would read or write the real ``data/tracker.db`` and
``data/calendar.ics``. Tests that need a particular engine still install their
own; they simply override this default.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from src import config
    from src.storage import database
    from src.storage.models import Base

    # In memory, on one shared connection (StaticPool), so every session in the
    # test sees the same data. A database file per test made the suite almost
    # three times slower on Windows.
    engine = create_engine(
        "sqlite://",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", database._enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(
        database, "SessionLocal", sessionmaker(bind=engine, future=True, expire_on_commit=False)
    )
    monkeypatch.setattr(config, "ICS_PATH", tmp_path / "calendar.ics")
    yield
    engine.dispose()
