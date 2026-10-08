"""The Flink job: one-minute metrics from the event stream, into metric_snapshots.

Reads the topic the worker's outbox relay writes, keeps per-minute counts in
keyed state (window_metrics.MetricsTracker), and every few seconds upserts the
minutes that have closed. On start it replays the last KEEP_MINUTES from Kafka,
so the System page's whole range is rebuilt rather than resumed half-counted.

The rows are upserted, so writing a minute twice — after a restart from a
checkpoint, or because a late event changed it — leaves one correct row.

Submitted to the session cluster by supervise.py; see compose.yaml.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

from pyflink.common import Types, WatermarkStrategy
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream import KeyedProcessFunction, RuntimeContext, StreamExecutionEnvironment
from pyflink.datastream.connectors.kafka import KafkaOffsetsInitializer, KafkaSource
from pyflink.datastream.state import ValueStateDescriptor

from window_metrics import MetricsTracker, Snapshot

JOB_NAME = "commitmail-metrics"
TICK_MS = 5_000

log = logging.getLogger(JOB_NAME)

UPSERT = """
    INSERT INTO metric_snapshots (source, "window", window_start, window_end, metrics, created_at)
    VALUES ('flink', '1m', %s, %s, %s, %s)
    ON CONFLICT (source, "window", window_start) DO UPDATE
       SET window_end = EXCLUDED.window_end,
           metrics = EXCLUDED.metrics,
           created_at = EXCLUDED.created_at
"""


def utc_naive(epoch_ms: int) -> datetime:
    return datetime.fromtimestamp(epoch_ms / 1000, timezone.utc).replace(tzinfo=None)


class SnapshotWriter:
    """Upserts windows into Postgres. Connection settings come from the task manager's environment."""

    def __init__(self) -> None:
        self.conn = None

    def write(self, snapshots: list[Snapshot]) -> None:
        import psycopg
        from psycopg.types.json import Jsonb

        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(
                host=os.environ.get("METRICS_DB_HOST", "postgres"),
                port=int(os.environ.get("METRICS_DB_PORT", "5432")),
                dbname=os.environ.get("METRICS_DB_NAME", "commitmail"),
                user=os.environ.get("METRICS_DB_USER", "commitmail"),
                password=os.environ["METRICS_DB_PASSWORD"],
                connect_timeout=5,
            )
        written_at = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.executemany(UPSERT, [
                (s.window_start, s.window_end, Jsonb(s.metrics), written_at) for s in snapshots
            ])

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()


class MinuteMetrics(KeyedProcessFunction):
    """Counts each event into its minute; on a processing-time tick, writes closed minutes.

    A failed write raises, so Flink restarts the job from its last checkpoint,
    where those minutes are still unwritten — none is skipped.
    """

    def open(self, runtime_context: RuntimeContext) -> None:
        self.state = runtime_context.get_state(ValueStateDescriptor("tracker", Types.STRING()))
        self.writer = SnapshotWriter()

    def close(self) -> None:
        self.writer.close()

    def _tracker(self) -> MetricsTracker:
        raw = self.state.value()
        return MetricsTracker.from_json(raw) if raw else MetricsTracker()

    def process_element(self, value, ctx):
        now_ms = ctx.timer_service().current_processing_time()
        try:
            event = json.loads(value)
        except (TypeError, ValueError):
            log.warning("skipped a message that is not JSON")
            return
        if not isinstance(event, dict):
            return
        tracker = self._tracker()
        if tracker.add(event, utc_naive(now_ms)):
            self.state.update(tracker.to_json())
        # Ticks fall on multiples of TICK_MS, so this only ever leaves one timer.
        ctx.timer_service().register_processing_time_timer((now_ms // TICK_MS + 1) * TICK_MS)

    def on_timer(self, timestamp, ctx):
        # Not `timestamp`: an overdue timer (after the machine slept) fires with
        # the time it was due, and stepping from there caught up 5 s per tick.
        now_ms = ctx.timer_service().current_processing_time()
        tracker = self._tracker()
        snapshots = tracker.due(utc_naive(now_ms))
        if snapshots:
            self.writer.write(snapshots)
            yield f"wrote {len(snapshots)} window(s), newest {snapshots[-1].window_start:%Y-%m-%d %H:%M} UTC"
        self.state.update(tracker.to_json())
        ctx.timer_service().register_processing_time_timer((now_ms // TICK_MS + 1) * TICK_MS)


def main() -> None:
    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(1)
    env.enable_checkpointing(60_000)

    replay_from_ms = int((time.time() - MetricsTracker.KEEP_MINUTES * 60) * 1000)
    source = (
        KafkaSource.builder()
        .set_bootstrap_servers(os.environ.get("KAFKA_BROKERS", "kafka:19092"))
        .set_topics(os.environ.get("KAFKA_EVENTS_TOPIC", "commitmail.events"))
        .set_group_id("commitmail-flink")
        .set_starting_offsets(KafkaOffsetsInitializer.timestamp(replay_from_ms))
        .set_value_only_deserializer(SimpleStringSchema())
        .set_property("partition.discovery.interval.ms", "60000")
        .build()
    )
    (
        env.from_source(source, WatermarkStrategy.no_watermarks(), "commitmail.events")
        .key_by(lambda _: 0, key_type=Types.INT())
        .process(MinuteMetrics(), output_type=Types.STRING())
        .name("one-minute windows")
        .print()
    )
    env.execute(JOB_NAME)


if __name__ == "__main__":
    main()
