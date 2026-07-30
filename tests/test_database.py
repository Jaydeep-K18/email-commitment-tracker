"""Unit tests for the storage layer using an in-memory SQLite database."""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from src.collection.email_parser import ParsedEmail
from src.storage import database
from src.storage.models import Base, RawEmail


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    db = TestSession()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _parsed(message_id: str = "m-1@example.com", **overrides) -> ParsedEmail:
    fields = dict(
        message_id=message_id,
        thread_id="t-1@example.com",
        sender_name="Ada",
        sender_email="ada@x.com",
        recipient_email="me@x.com",
        subject="Hello",
        body_text="Deadline Friday.",
        received_at=datetime(2025, 7, 22, 9, 0, 0),
    )
    fields.update(overrides)
    return ParsedEmail(**fields)


def _count(session) -> int:
    return session.execute(select(func.count()).select_from(RawEmail)).scalar_one()


def test_save_email_inserts_with_defaults(session):
    saved = database.save_email(session, _parsed())
    session.commit()
    assert saved is not None
    assert saved.id is not None
    assert saved.processed is False
    assert saved.vip_tier is None
    assert saved.fetched_at is not None
    assert _count(session) == 1


def test_save_email_dedup_on_message_id(session):
    assert database.save_email(session, _parsed()) is not None
    session.commit()
    # Same Message-ID again returns None and adds no row.
    assert database.save_email(session, _parsed()) is None
    session.commit()
    assert _count(session) == 1


def test_save_email_distinct_ids_both_stored(session):
    database.save_email(session, _parsed("a@example.com"))
    database.save_email(session, _parsed("b@example.com"))
    session.commit()
    assert _count(session) == 2


def test_email_exists(session):
    assert database.email_exists(session, "m-1@example.com") is False
    database.save_email(session, _parsed())
    session.commit()
    assert database.email_exists(session, "m-1@example.com") is True
