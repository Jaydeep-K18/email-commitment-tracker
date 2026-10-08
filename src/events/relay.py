"""The outbox relay: hand committed events to Kafka.

:func:`src.events.recorder.record_event` only ever writes a row, in the same
transaction as the change it describes. This is the other half of that
transactional outbox. It reads events whose ``published_at`` is still NULL,
publishes each one to the events topic, and stamps ``published_at`` once Kafka
has acknowledged it.

What that guarantees:

* **Nothing uncommitted is published.** The relay only ever sees committed rows,
  so a change that rolled back never reaches a consumer.
* **Nothing committed is lost.** A row is marked only after Kafka acknowledged
  it. A crash in between means it is published again: delivery is *at least
  once*, and every consumer is idempotent by event id (the server's notifier
  has ``UNIQUE(event_id, kind)``; the browser drops an id it already has).
* **One email's story stays in order.** Messages are keyed by
  ``correlation_id``, so received → classified → analysed → on the calendar for
  one email lands on one partition, in the order it happened.

The message is the row itself, with the events table's column names. Consumers
already know that shape — the server maps a Kafka message with the same
function it uses for a row read from Postgres — so there is one schema, the
table's, and ``packages/shared/contracts/event-message.json`` pins it.

It runs inside the worker whenever ``KAFKA_BROKERS`` is set, or on its own::

    python -m src.events.relay
"""
from __future__ import annotations

import json
import logging
import signal
import sys
import threading
from typing import Any, Protocol

from sqlalchemy import select

from src import config
from src.storage.database import session_scope
from src.storage.models import Event, utcnow_naive

log = logging.getLogger(__name__)

#: Partitions for a newly created topic. Ordering only matters within one
#: email's events, so a few partitions cost nothing and leave room to add
#: consumers later.
TOPIC_PARTITIONS = 3
#: Events per round trip. A backlog (the first start, or after Kafka was down)
#: drains in batches of this size, one transaction each.
BATCH_SIZE = 500
#: Longer than the producer's own delivery timeout, so every message has a
#: delivery report — success or failure — by the time flush returns.
FLUSH_TIMEOUT = 15.0


class Producer(Protocol):
    """The part of ``confluent_kafka.Producer`` the relay uses."""

    def produce(self, topic: str, value: bytes, key: bytes, on_delivery: Any) -> None: ...
    def poll(self, timeout: float) -> int: ...
    def flush(self, timeout: float) -> int: ...


def message_for(event: Event) -> dict[str, Any]:
    """An event as it travels on Kafka: the row, under its own column names.

    ``created_at`` stays naive UTC, as stored; consumers convert it exactly as
    they convert a timestamp read from the table.
    """
    return {
        "id": event.id,
        "type": event.type,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "correlation_id": event.correlation_id,
        "severity": event.severity,
        "message": event.message,
        "payload": event.payload or {},
        "source": event.source,
        "created_at": event.created_at.isoformat(),
    }


def key_for(event: Event) -> str:
    """The partition key: one email's events together, else one entity's."""
    if event.correlation_id:
        return event.correlation_id
    if event.entity_type and event.entity_id:
        return f"{event.entity_type}:{event.entity_id}"
    return f"event:{event.id}"


class Relay:
    def __init__(
        self,
        producer: Producer,
        topic: str = config.KAFKA_EVENTS_TOPIC,
        batch_size: int = BATCH_SIZE,
        flush_timeout: float = FLUSH_TIMEOUT,
    ) -> None:
        self.producer = producer
        self.topic = topic
        self.batch_size = batch_size
        self.flush_timeout = flush_timeout

    def publish_pending(self) -> int:
        """Publish one batch of unpublished events; return how many Kafka took.

        The rows are locked (``SKIP LOCKED``) for the length of the batch, so a
        second relay — two workers, say — takes different rows instead of
        publishing the same ones twice. Only events Kafka acknowledged are
        marked; the rest stay NULL and are tried again on the next pass.
        """
        delivered: set[int] = set()
        failures: list[str] = []

        def report(event_id: int):
            def on_delivery(err, _msg) -> None:  # noqa: ANN001 - librdkafka types
                if err is None:
                    delivered.add(event_id)
                else:
                    failures.append(f"event {event_id}: {err}")
            return on_delivery

        with session_scope() as session:
            events = list(
                session.scalars(
                    select(Event)
                    .where(Event.published_at.is_(None))
                    .order_by(Event.id)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            if not events:
                return 0

            for event in events:
                self.producer.produce(
                    self.topic,
                    value=json.dumps(message_for(event), separators=(",", ":")).encode(),
                    key=key_for(event).encode(),
                    on_delivery=report(event.id),
                )
                self.producer.poll(0)   # serve delivery reports as the queue fills
            self.producer.flush(self.flush_timeout)

            now = utcnow_naive()
            for event in events:
                if event.id in delivered:
                    event.published_at = now

        if failures:
            log.warning(
                "Kafka did not take %d of %d event(s); they will be retried. First: %s",
                len(failures), len(events), failures[0],
            )
        return len(delivered)


def create_producer(brokers: list[str]):
    from confluent_kafka import Producer as KafkaProducer

    return KafkaProducer(
        {
            "bootstrap.servers": ",".join(brokers),
            "client.id": "commitmail-relay",
            # Retries never duplicate or reorder within a partition.
            "enable.idempotence": True,
            "acks": "all",
            "linger.ms": 5,
            # Give up on a message well inside FLUSH_TIMEOUT, so the relay always
            # learns its fate and can leave it unmarked for the next pass.
            "delivery.timeout.ms": 10_000,
        }
    )


def ensure_topic(brokers: list[str], topic: str, partitions: int = TOPIC_PARTITIONS) -> bool:
    """Create the topic if it is missing. True once it exists.

    The broker does not create topics on first use (compose turns that off),
    so the relay creates the one it writes to. Replication is left to the
    broker's default, which is right for one broker or for a cluster.
    """
    from confluent_kafka import KafkaError, KafkaException
    from confluent_kafka.admin import AdminClient, NewTopic

    admin = AdminClient({"bootstrap.servers": ",".join(brokers)})
    futures = admin.create_topics(
        [NewTopic(topic, num_partitions=partitions, replication_factor=-1)],
        request_timeout=10,
    )
    try:
        futures[topic].result(timeout=15)
        log.info("Created Kafka topic %s with %d partitions.", topic, partitions)
    except KafkaException as exc:
        if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise
    return True


class RelayLoop:
    """Keep the outbox drained until ``stop`` is set.

    Kafka being down is an expected state, not an error to repeat every few
    seconds: it is logged once when it starts and once when it ends, and the
    events simply wait in Postgres meanwhile.
    """

    def __init__(
        self,
        brokers: list[str],
        stop: threading.Event,
        topic: str = config.KAFKA_EVENTS_TOPIC,
        interval: float = config.KAFKA_RELAY_INTERVAL_MS / 1000,
        retry_delay: float = 5.0,
    ) -> None:
        self.brokers = brokers
        self.stop = stop
        self.topic = topic
        self.interval = interval
        self.retry_delay = retry_delay
        self.relay: Relay | None = None
        self.failing = False
        self.published = 0

    def run(self) -> None:
        log.info("Relaying events to Kafka at %s, topic %s.", ",".join(self.brokers), self.topic)
        while not self.stop.is_set():
            try:
                if self.relay is None:
                    ensure_topic(self.brokers, self.topic)
                    self.relay = Relay(create_producer(self.brokers), self.topic)
                sent = self.relay.publish_pending()
            except Exception as exc:  # noqa: BLE001 - Kafka or Postgres unreachable
                if not self.failing:
                    log.warning("Cannot relay events to Kafka yet (%s); retrying.", exc)
                    self.failing = True
                self.stop.wait(self.retry_delay)
                continue
            if self.failing:
                log.info("Relaying events to Kafka again.")
                self.failing = False
            self.published += sent
            # A full batch means there is probably more waiting: go straight on.
            if sent < BATCH_SIZE:
                self.stop.wait(self.interval)
        if self.relay is not None:
            self.relay.producer.flush(5)


def start_relay_thread(stop: threading.Event, brokers: list[str] | None = None) -> threading.Thread:
    loop = RelayLoop(brokers or config.KAFKA_BROKERS, stop)
    thread = threading.Thread(target=loop.run, name="outbox-relay", daemon=True)
    thread.start()
    return thread


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    if not config.KAFKA_BROKERS:
        print("KAFKA_BROKERS is not set. Set it in .env (e.g. 127.0.0.1:9092) and run again.")
        return 1
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    RelayLoop(config.KAFKA_BROKERS, stop).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
