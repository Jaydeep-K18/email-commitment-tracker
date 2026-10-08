"""One-minute metrics over the event stream: the rules, without Flink.

Two things compute the System page's metrics, and both follow this module: the
Flink job (job.py) over the Kafka topic, and the server's fallback over the
events table — the same stream at rest. scripts/export_contracts.py writes cases
from here to packages/shared/contracts/metric-windows.json, which the TypeScript
copy (packages/shared/src/metrics.ts) is tested against, so the page shows the
same numbers whichever one produced them.

Plain Python on purpose: it runs inside the Flink image (Python 3.10) and is
tested without Java.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Spikes and anomalies are judged against the trailing hour, this minute included.
BASELINE_MINUTES = 60

PROCESSED = "email.analyzed"
SUCCEEDED = "job.completed"
FAILED_ATTEMPTS = ("job.retrying", "job.failed")


@dataclass
class MinuteCounts:
    events: int = 0
    processed: int = 0
    succeeded: int = 0
    failures: int = 0
    durations_ms: list[float] = field(default_factory=list)

    def add(self, event_type: str, payload: dict) -> None:
        self.events += 1
        if event_type == PROCESSED:
            self.processed += 1
        elif event_type == SUCCEEDED:
            self.succeeded += 1
            duration = payload.get("duration_ms")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                self.durations_ms.append(float(duration))
        elif event_type in FAILED_ATTEMPTS:
            self.failures += 1


def half_up(value: float) -> int:
    """JavaScript's Math.round — Python's round() rounds halves to even."""
    return math.floor(value + 0.5)


def percentile_cont(values: list[float], fraction: float) -> float:
    """Postgres's percentile_cont: linear interpolation between neighbours."""
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def window_metrics(series: list[MinuteCounts]) -> list[dict]:
    """Metrics for each minute of a contiguous series, oldest first.

    A minute's baseline is the BASELINE_MINUTES ending with it, as far as the
    series reaches — so callers pass BASELINE_MINUTES - 1 extra minutes in front
    of the ones they want.
    """
    results = []
    for i, minute in enumerate(series):
        baseline = series[max(0, i - BASELINE_MINUTES + 1): i + 1]
        mean_failures = sum(m.failures for m in baseline) / len(baseline)
        mean_events = sum(m.events for m in baseline) / len(baseline)
        sd = math.sqrt(sum((m.events - mean_events) ** 2 for m in baseline) / len(baseline))
        attempts = minute.succeeded + minute.failures
        durations = minute.durations_ms
        results.append({
            "events": minute.events,
            "emailsProcessed": minute.processed,
            "throughputPerMinute": minute.processed,
            "avgLatencyMs": half_up(sum(durations) / len(durations)) if durations else None,
            "p95LatencyMs": half_up(percentile_cont(durations, 0.95)) if durations else None,
            "successRate": half_up(minute.succeeded / attempts * 1000) / 10 if attempts else None,
            "failures": minute.failures,
            # At least 3 failures, and three times the hour's average.
            "errorSpike": minute.failures >= 3 and minute.failures >= 3 * mean_failures,
            # At least 10 events, more than three standard deviations above the hour.
            "volumeAnomaly": sd > 0 and minute.events >= 10 and (minute.events - mean_events) / sd > 3,
        })
    return results


@dataclass
class Snapshot:
    window_start: datetime
    window_end: datetime
    metrics: dict


EPOCH = datetime(1970, 1, 1)


def epoch_minute(moment: datetime) -> int:
    return int((moment - EPOCH).total_seconds() // 60)


def minute_start(minute: int) -> datetime:
    return EPOCH + timedelta(minutes=minute)


class MetricsTracker:
    """The Flink job's state: counts per minute, and which minutes are written.

    Minutes are closed by the wall clock, not by watermarks. Event-time windows
    only close when a later event arrives, and this stream is idle most of the
    time, so the newest minute would wait — and an empty minute would never be
    written at all. Here a minute is written GRACE_SECONDS after it ends, empty
    or not. An event that arrives later still counts: its minute (and the ones
    after it, whose baselines include it) are written again, over the old rows.

    Timestamps are naive UTC, like the events table.
    """

    KEEP_MINUTES = 300        # replayed from Kafka on start; older events are ignored
    BACKFILL_MINUTES = 240    # written on start: the System page's longest range
    GRACE_SECONDS = 15        # how long a minute stays open for stragglers

    def __init__(self) -> None:
        self.minutes: dict[int, MinuteCounts] = {}
        self.seen: dict[int, set[int]] = {}
        self.written_through: int | None = None
        self.dirty_from: int | None = None

    def add(self, event: dict, now: datetime) -> bool:
        """Count one event from the topic. False if it was ignored."""
        try:
            minute = epoch_minute(datetime.fromisoformat(event["created_at"]))
            event_id, event_type = int(event["id"]), str(event["type"])
        except (KeyError, TypeError, ValueError):
            return False
        if minute < epoch_minute(now) - self.KEEP_MINUTES:
            return False
        # The relay delivers at least once; the events table counts each row once.
        ids = self.seen.setdefault(minute, set())
        if event_id in ids:
            return False
        ids.add(event_id)
        payload = event.get("payload")
        self.minutes.setdefault(minute, MinuteCounts()).add(event_type, payload if isinstance(payload, dict) else {})
        if self.written_through is not None and minute <= self.written_through:
            self.dirty_from = minute if self.dirty_from is None else min(self.dirty_from, minute)
        return True

    def due(self, now: datetime) -> list[Snapshot]:
        """The closed minutes to write now, and mark them written."""
        last_closed = epoch_minute(now - timedelta(seconds=60 + self.GRACE_SECONDS))
        earliest = epoch_minute(now) - self.BACKFILL_MINUTES
        start = earliest if self.written_through is None else self.written_through + 1
        if self.dirty_from is not None:
            start = min(start, self.dirty_from)
        start = max(start, earliest)
        snapshots = []
        if start <= last_closed:
            span = range(start - BASELINE_MINUTES + 1, last_closed + 1)
            metrics = window_metrics([self.minutes.get(m) or MinuteCounts() for m in span])
            for minute, values in zip(span[BASELINE_MINUTES - 1:], metrics[BASELINE_MINUTES - 1:]):
                snapshots.append(Snapshot(minute_start(minute), minute_start(minute + 1), values))
            self.written_through = last_closed
        self.dirty_from = None
        self._forget_before(epoch_minute(now) - self.KEEP_MINUTES)
        return snapshots

    def _forget_before(self, minute: int) -> None:
        for old in [m for m in self.minutes if m < minute]:
            del self.minutes[old]
        for old in [m for m in self.seen if m < minute]:
            del self.seen[old]

    # Flink keeps the tracker in keyed state as JSON: readable in a checkpoint,
    # and not tied to this class's layout the way a pickle would be.

    def to_json(self) -> str:
        return json.dumps({
            "minutes": {str(m): vars(c) for m, c in self.minutes.items()},
            "seen": {str(m): sorted(ids) for m, ids in self.seen.items()},
            "writtenThrough": self.written_through,
            "dirtyFrom": self.dirty_from,
        })

    @classmethod
    def from_json(cls, raw: str) -> "MetricsTracker":
        data = json.loads(raw)
        tracker = cls()
        tracker.minutes = {int(m): MinuteCounts(**c) for m, c in data["minutes"].items()}
        tracker.seen = {int(m): set(ids) for m, ids in data["seen"].items()}
        tracker.written_through = data["writtenThrough"]
        tracker.dirty_from = data["dirtyFrom"]
        return tracker
