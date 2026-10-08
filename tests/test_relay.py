"""Tests for the outbox relay (v2 phase 6).

Kafka itself is replaced by a producer that records what it was given and can
be told to refuse messages, because the relay's promises are about what it
marks, not about the broker: an event Kafka did not acknowledge must stay
unpublished, and one it did must never be sent again.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime

from sqlalchemy import select

from src.events import relay as relay_module
from src.events.recorder import EMAIL_CLASSIFIED, EMAIL_RECEIVED, JOB_FAILED, record_event
from src.events.relay import Relay, RelayLoop, key_for, message_for
from src.storage.database import session_scope
from src.storage.models import Event


class FakeProducer:
    def __init__(self, refuse: set[int] = frozenset(), lose: set[int] = frozenset()):
        self.refuse = set(refuse)   # delivery report says it failed
        self.lose = set(lose)       # no report at all (flush timed out)
        self.sent: list[dict] = []
        self._pending: list = []

    def produce(self, topic, value, key, on_delivery):
        message = json.loads(value)
        self.sent.append({"topic": topic, "key": key.decode(), "value": message})
        if message["id"] not in self.lose:
            error = "broker said no" if message["id"] in self.refuse else None
            self._pending.append((on_delivery, error))

    def poll(self, timeout):
        return 0

    def flush(self, timeout):
        for callback, error in self._pending:
            callback(error, None)
        self._pending.clear()
        return 0


def add_events(*specs) -> list[int]:
    ids = []
    with session_scope() as session:
        for type_, correlation in specs:
            event = record_event(session, type_, f"{type_} happened", correlation_id=correlation,
                                 entity_type="email", entity_id=7, payload={"n": 1})
            session.flush()
            ids.append(event.id)
    return ids


def unpublished() -> list[int]:
    with session_scope() as session:
        return list(session.scalars(
            select(Event.id).where(Event.published_at.is_(None)).order_by(Event.id)
        ))


def test_events_are_published_in_order_and_marked():
    ids = add_events((EMAIL_RECEIVED, "email:7"), (EMAIL_CLASSIFIED, "email:7"), (JOB_FAILED, None))
    producer = FakeProducer()

    assert Relay(producer, "t").publish_pending() == 3
    assert [m["value"]["id"] for m in producer.sent] == ids
    assert {m["topic"] for m in producer.sent} == {"t"}
    assert unpublished() == []


def test_a_published_event_is_never_sent_again():
    add_events((EMAIL_RECEIVED, "email:1"))
    producer = FakeProducer()
    relay = Relay(producer, "t")
    relay.publish_pending()

    assert relay.publish_pending() == 0
    assert len(producer.sent) == 1


def test_an_event_kafka_refused_stays_waiting_and_goes_next_time():
    first, second, third = add_events(
        (EMAIL_RECEIVED, "email:1"), (EMAIL_RECEIVED, "email:2"), (EMAIL_RECEIVED, "email:3")
    )
    assert Relay(FakeProducer(refuse={second}), "t").publish_pending() == 2
    assert unpublished() == [second]

    retry = FakeProducer()
    assert Relay(retry, "t").publish_pending() == 1
    assert [m["value"]["id"] for m in retry.sent] == [second]
    assert unpublished() == []


def test_an_event_with_no_delivery_report_is_not_assumed_delivered():
    """flush() timing out leaves messages unreported. Marking them would be a
    guess, and a wrong guess loses the event for good."""
    (only,) = add_events((EMAIL_RECEIVED, "email:1"))
    assert Relay(FakeProducer(lose={only}), "t").publish_pending() == 0
    assert unpublished() == [only]


def test_a_failure_before_flush_marks_nothing():
    add_events((EMAIL_RECEIVED, "email:1"), (EMAIL_RECEIVED, "email:2"))

    class Exploding(FakeProducer):
        def flush(self, timeout):
            raise RuntimeError("broker gone")

    try:
        Relay(Exploding(), "t").publish_pending()
    except RuntimeError:
        pass
    assert len(unpublished()) == 2


def test_a_backlog_drains_in_batches():
    add_events(*[(EMAIL_RECEIVED, f"email:{i}") for i in range(5)])
    relay = Relay(FakeProducer(), "t", batch_size=2)
    assert [relay.publish_pending() for _ in range(4)] == [2, 2, 1, 0]


def test_one_emails_story_shares_a_key_so_it_stays_in_order():
    with session_scope() as session:
        email_event = record_event(session, EMAIL_RECEIVED, "x", correlation_id="email:42")
        job_event = record_event(session, JOB_FAILED, "y", entity_type="job", entity_id=9)
        bare = record_event(session, JOB_FAILED, "z")
        session.flush()
        assert key_for(email_event) == "email:42"
        assert key_for(job_event) == "job:9"
        assert key_for(bare) == f"event:{bare.id}"


def test_the_message_is_the_row_under_its_own_column_names():
    """The server maps a Kafka message with the function it uses for table
    rows, so the message must look exactly like a row."""
    event = Event(
        id=12, type=EMAIL_RECEIVED, entity_type="email", entity_id="7", correlation_id="email:7",
        severity="info", message="Email from Priya", payload={"vip_tier": "CRITICAL"},
        source="worker", created_at=datetime(2026, 10, 8, 9, 30, 0, 123456),
    )
    assert message_for(event) == {
        "id": 12, "type": "email.received", "entity_type": "email", "entity_id": "7",
        "correlation_id": "email:7", "severity": "info", "message": "Email from Priya",
        "payload": {"vip_tier": "CRITICAL"}, "source": "worker",
        "created_at": "2026-10-08T09:30:00.123456",
    }


def test_the_loop_survives_kafka_being_down_and_says_so_once(monkeypatch, caplog):
    attempts = []
    stop = threading.Event()

    def unreachable(brokers, topic, partitions=3):
        attempts.append(1)
        if len(attempts) >= 3:
            stop.set()
        raise RuntimeError("no broker")

    monkeypatch.setattr(relay_module, "ensure_topic", unreachable)
    with caplog.at_level("WARNING", logger="src.events.relay"):
        RelayLoop(["127.0.0.1:9"], stop, topic="t", retry_delay=0).run()

    assert len(attempts) == 3
    assert sum("Cannot relay" in r.message for r in caplog.records) == 1


def test_the_loop_publishes_until_stopped(monkeypatch):
    add_events((EMAIL_RECEIVED, "email:1"), (EMAIL_RECEIVED, "email:2"))
    producer = FakeProducer()
    stop = threading.Event()
    monkeypatch.setattr(relay_module, "ensure_topic", lambda *a, **k: True)
    monkeypatch.setattr(relay_module, "create_producer", lambda brokers: producer)

    loop = RelayLoop(["b:1"], stop, topic="t", interval=0.01)
    thread = threading.Thread(target=loop.run)
    thread.start()
    for _ in range(200):
        if loop.published == 2:
            break
        stop.wait(0.01)
    stop.set()
    thread.join(5)

    assert loop.published == 2
    assert unpublished() == []
