"""v2 phase 7: the rules the Flink job computes its windows by, without Flink."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from streaming.window_metrics import (
    BASELINE_MINUTES,
    MetricsTracker,
    MinuteCounts,
    half_up,
    percentile_cont,
    window_metrics,
)

NOW = datetime(2026, 10, 8, 12, 0, 30)


def event(event_id: int, at: datetime, type_: str = "email.received", **payload) -> dict:
    return {"id": event_id, "type": type_, "created_at": at.isoformat(), "payload": payload}


def quiet(n: int) -> list[MinuteCounts]:
    return [MinuteCounts() for _ in range(n)]


# --- What a minute counts ----------------------------------------------------------

def test_a_minute_counts_events_processed_mail_and_job_attempts():
    minute = MinuteCounts()
    for type_, payload in [
        ("email.received", {}),
        ("email.analyzed", {}),
        ("job.completed", {"duration_ms": 120}),
        ("job.completed", {"duration_ms": "fast"}),   # not a number: not a latency
        ("job.retrying", {}),
        ("job.failed", {}),
        ("job.queued", {}),
    ]:
        minute.add(type_, payload)
    assert (minute.events, minute.processed, minute.succeeded, minute.failures) == (7, 1, 2, 2)
    assert minute.durations_ms == [120.0]


# --- The arithmetic, as the TypeScript and Postgres sides do it ----------------------

def test_rounding_is_javascripts_not_pythons():
    assert [half_up(x) for x in (0.5, 1.5, 2.5, -0.5)] == [1, 2, 3, 0]


@pytest.mark.parametrize("values, expected", [
    ([5], 5),
    (list(range(1, 21)), 19.05),      # SELECT percentile_cont(0.95) ... generate_series(1, 20)
    ([100, 300], 290),
])
def test_p95_interpolates_like_postgres(values, expected):
    assert percentile_cont(values, 0.95) == pytest.approx(expected)


def test_an_empty_minute_has_no_latency_or_success_rate():
    assert window_metrics(quiet(1)) == [{
        "events": 0, "emailsProcessed": 0, "throughputPerMinute": 0,
        "avgLatencyMs": None, "p95LatencyMs": None, "successRate": None,
        "failures": 0, "errorSpike": False, "volumeAnomaly": False,
    }]


def test_success_rate_is_a_percentage_to_one_decimal():
    [metrics] = window_metrics([MinuteCounts(succeeded=2, failures=1)])
    assert metrics["successRate"] == 66.7


# --- Spikes and anomalies -----------------------------------------------------------

def test_failures_in_a_quiet_hour_are_a_spike():
    *_, last = window_metrics(quiet(BASELINE_MINUTES - 1) + [MinuteCounts(failures=3)])
    assert last["errorSpike"] is True


def test_the_same_failures_in_a_failing_hour_are_not():
    hour = [MinuteCounts(failures=2) for _ in range(BASELINE_MINUTES - 1)] + [MinuteCounts(failures=3)]
    assert window_metrics(hour)[-1]["errorSpike"] is False


def test_a_burst_of_activity_is_an_anomaly_but_steady_activity_is_not():
    steady = [MinuteCounts(events=3) for _ in range(BASELINE_MINUTES - 1)]
    assert window_metrics(steady + [MinuteCounts(events=30)])[-1]["volumeAnomaly"] is True
    assert window_metrics(steady + [MinuteCounts(events=4)])[-1]["volumeAnomaly"] is False


def test_the_baseline_is_the_trailing_hour_only():
    """A spike ninety minutes ago no longer raises the bar."""
    old_storm = [MinuteCounts(failures=50)] + quiet(BASELINE_MINUTES - 1)
    assert window_metrics(old_storm + [MinuteCounts(failures=3)])[-1]["errorSpike"] is True


# --- The tracker: which minutes the job writes, and when ------------------------------

def test_on_start_it_writes_the_last_four_hours_including_empty_minutes():
    tracker = MetricsTracker()
    tracker.add(event(1, NOW - timedelta(minutes=10)), NOW)

    written = tracker.due(NOW)

    assert len(written) == MetricsTracker.BACKFILL_MINUTES      # 08:00 up to 11:59, which closed at 12:00:15
    assert written[-1].window_start == datetime(2026, 10, 8, 11, 59)
    assert [w.window_end - w.window_start for w in written[:1]] == [timedelta(minutes=1)]
    assert sum(w.metrics["events"] for w in written) == 1
    assert all(b.window_start == a.window_end for a, b in zip(written, written[1:]))


def test_a_minute_closes_once_its_grace_period_is_over():
    tracker = MetricsTracker()
    tracker.due(datetime(2026, 10, 8, 12, 0, 5))        # 11:58 is the newest closed minute
    tracker.add(event(1, datetime(2026, 10, 8, 11, 59, 50)), NOW)

    assert tracker.due(datetime(2026, 10, 8, 12, 0, 14)) == []
    [closed] = tracker.due(datetime(2026, 10, 8, 12, 0, 15))
    assert closed.window_start == datetime(2026, 10, 8, 11, 59)
    assert closed.metrics["events"] == 1


def test_an_idle_stream_still_writes_every_minute():
    """The server reads a stale newest window as 'Flink is down'."""
    tracker = MetricsTracker()
    tracker.due(NOW)
    later = [tracker.due(NOW + timedelta(minutes=m)) for m in (1, 2, 3)]
    assert [len(batch) for batch in later] == [1, 1, 1]
    assert all(batch[0].metrics["events"] == 0 for batch in later)


def test_an_event_delivered_twice_counts_once():
    tracker = MetricsTracker()
    at = NOW - timedelta(minutes=5)
    assert tracker.add(event(7, at), NOW) is True
    assert tracker.add(event(7, at), NOW) is False
    assert sum(s.metrics["events"] for s in tracker.due(NOW)) == 1


def test_a_late_event_rewrites_its_minute_and_the_ones_judged_against_it():
    tracker = MetricsTracker()
    tracker.due(NOW)
    late = NOW - timedelta(minutes=3)
    tracker.add(event(9, late), NOW)

    rewritten = tracker.due(NOW + timedelta(seconds=5))

    assert rewritten[0].window_start == late.replace(second=0)
    assert rewritten[0].metrics["events"] == 1
    assert rewritten[-1].window_start == datetime(2026, 10, 8, 11, 59)


def test_events_older_than_the_replay_are_ignored_and_old_state_is_dropped():
    tracker = MetricsTracker()
    assert tracker.add(event(1, NOW - timedelta(minutes=MetricsTracker.KEEP_MINUTES + 1)), NOW) is False
    tracker.add(event(2, NOW - timedelta(minutes=30)), NOW)
    tracker.due(NOW + timedelta(minutes=MetricsTracker.KEEP_MINUTES))
    assert tracker.minutes == {} and tracker.seen == {}


def test_malformed_events_are_skipped():
    tracker = MetricsTracker()
    assert tracker.add({"type": "email.received"}, NOW) is False
    assert tracker.add({"id": 1, "type": "x", "created_at": "yesterday"}, NOW) is False


def test_the_state_survives_a_checkpoint():
    tracker = MetricsTracker()
    tracker.add(event(1, NOW - timedelta(minutes=2), "job.completed", duration_ms=250), NOW)
    tracker.due(NOW)
    tracker.add(event(2, NOW - timedelta(minutes=4)), NOW)

    restored = MetricsTracker.from_json(tracker.to_json())

    assert restored.minutes == tracker.minutes
    assert restored.seen == tracker.seen
    assert (restored.written_through, restored.dirty_from) == (tracker.written_through, tracker.dirty_from)
    assert [s.metrics for s in restored.due(NOW)] == [s.metrics for s in tracker.due(NOW)]
