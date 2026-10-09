"""Write the facts Node must agree with Python on, as JSON, for both to test.

    python -m scripts.export_contracts

Two languages share one database, so a handful of things must be identical on
both sides: the event types, the categories and tiers, the job types, the shape
of an event on Kafka, and — the subtle one — the tier policy that decides which
commitments go on the calendar.
Python is the source of truth for all of them. This writes them to
``packages/shared/contracts/``; the TypeScript tests then check the shared
package against those files, and a Python test checks the files are current.
Change one side without the other and a test fails on whichever side drifted.
"""
from __future__ import annotations

import itertools
import json
import sys
from datetime import datetime
from pathlib import Path

from src import config
from src.classification.classifier import CATEGORIES, CATEGORY_RANK
from src.events.recorder import EVENT_TYPES, SEVERITIES
from src.extraction.schemas import CommitmentType
from src.filtering.vip_filter import MATCH_TYPES, TIERS
from src.jobs.handlers import HANDLERS
from src.storage.models import Commitment
from src.sync.sync_engine import decide

CONTRACTS_DIR = config.BASE_DIR / "packages" / "shared" / "contracts"

COMMITMENT_STATUSES = ("pending", "fulfilled", "overdue", "dismissed", "superseded")
JOB_STATUSES = ("queued", "running", "retrying", "succeeded", "failed", "cancelled")


def catalogue() -> dict:
    return {
        "eventTypes": list(EVENT_TYPES),
        "severities": list(SEVERITIES),
        "categories": list(CATEGORIES),
        "categoryRank": CATEGORY_RANK,
        "tiers": list(TIERS),
        "matchTypes": list(MATCH_TYPES),
        "commitmentTypes": [t.value for t in CommitmentType],
        "commitmentStatuses": list(COMMITMENT_STATUSES),
        "jobTypes": sorted(HANDLERS),
        "jobStatuses": list(JOB_STATUSES),
        "redisKeys": {
            "ready": "{prefix}:jobs:ready",
            "processing": "{prefix}:jobs:processing",
            "delayed": "{prefix}:jobs:delayed",
        },
    }


def sync_decisions() -> list[dict]:
    """``decide()`` evaluated over every combination of the inputs it reads."""
    cases = []
    for type_, has_deadline, status, tier, manual, approved in itertools.product(
        ("deadline_on_you", "question_pending"),
        (True, False),
        COMMITMENT_STATUSES,
        (*TIERS, None),
        (False, True),
        (False, True),
    ):
        commitment = Commitment(
            type=type_,
            deadline=datetime(2026, 9, 1, 17, 0) if has_deadline else None,
            status=status,
            vip_tier=tier,
            manually_added=manual,
            sync_approved=approved,
        )
        decision = decide(commitment)
        cases.append({
            "input": {
                "type": type_,
                "hasDeadline": has_deadline,
                "status": status,
                "vipTier": tier,
                "manuallyAdded": manual,
                "syncApproved": approved,
            },
            "output": {
                "shouldSync": decision.should_sync,
                "reason": decision.reason,
                "needsReview": decision.needs_review,
                "awaitingApproval": decision.awaiting_approval,
            },
        })
    return cases


def postgres_schema() -> str:
    """The full Postgres DDL, as Alembic would run it, without a database.

    The Node server's tests load this into PGlite (Postgres in WebAssembly), so
    they run against the real schema — triggers, generated columns and all —
    rather than a hand-written approximation of it.
    """
    import io

    from alembic import command
    from alembic.config import Config

    buffer = io.StringIO()
    cfg = Config(str(config.BASE_DIR / "alembic.ini"), output_buffer=buffer)
    cfg.set_main_option("script_location", str(config.BASE_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://contract@localhost/contract")
    command.upgrade(cfg, "head", sql=True)
    return buffer.getvalue()


def event_message() -> dict:
    """One event exactly as the outbox relay puts it on Kafka.

    The server's tests parse this file with the function its Kafka feed uses,
    so a change to the message on either side fails a test on that side.
    """
    from src.events.relay import message_for
    from src.storage.models import Event

    return message_for(
        Event(
            id=4101,
            user_id=7,
            type="email.received",
            entity_type="email",
            entity_id="42",
            correlation_id="email:42",
            severity="info",
            message="Email from Priya Nair: Q3 report",
            payload={"subject": "Q3 report", "vip_tier": "CRITICAL"},
            source="worker",
            created_at=datetime(2026, 10, 8, 9, 30, 0, 123456),
        )
    )


def metric_windows() -> dict:
    """The System page's metric rules, worked through a set of minutes.

    The Flink job computes them in Python (streaming/window_metrics.py); the
    server's fallback in TypeScript. Both must give the same numbers.
    """
    import random

    from streaming.window_metrics import BASELINE_MINUTES, MinuteCounts, window_metrics

    rng = random.Random(7)
    quiet = [MinuteCounts() for _ in range(BASELINE_MINUTES - 1)]
    steady = [MinuteCounts(events=3, succeeded=1) for _ in range(BASELINE_MINUTES - 1)]
    cases = {
        "an empty minute": [MinuteCounts()],
        "latency: the mean, and the 95th percentile interpolated like percentile_cont": [
            MinuteCounts(events=5, succeeded=5, durations_ms=[400, 100, 1000, 300, 200]),
            MinuteCounts(events=2, succeeded=2, durations_ms=[0.5, 2.5]),
        ],
        "success rate: one decimal, halves rounded up": [
            MinuteCounts(events=3, succeeded=2, failures=1),
            MinuteCounts(events=8, succeeded=1, failures=7),
            MinuteCounts(events=1, failures=1),
        ],
        "an error spike in a quiet hour": quiet + [MinuteCounts(events=3, failures=3)],
        "a volume anomaly against a steady hour": steady + [MinuteCounts(events=30, succeeded=1)],
        "ninety random minutes, so the baseline slides": [
            MinuteCounts(
                events=rng.choice([0, 0, 1, 2, 5, 40]),
                processed=rng.randint(0, 2),
                succeeded=rng.randint(0, 3),
                failures=rng.choice([0, 0, 0, 1, 4]),
                durations_ms=[rng.randint(5, 900) for _ in range(rng.randint(0, 4))],
            )
            for _ in range(90)
        ],
    }
    return {
        "baselineMinutes": BASELINE_MINUTES,
        "cases": [
            {
                "name": name,
                "series": [
                    {
                        "events": m.events,
                        "processed": m.processed,
                        "succeeded": m.succeeded,
                        "failures": m.failures,
                        "durationsMs": m.durations_ms,
                    }
                    for m in series
                ],
                "metrics": window_metrics(series),
            }
            for name, series in cases.items()
        ],
    }


def render() -> dict[str, str]:
    """File name -> exact contents. Deterministic, so it can be diffed."""
    def dump(value) -> str:
        return json.dumps(value, indent=2, sort_keys=True) + "\n"

    return {
        "catalogue.json": dump(catalogue()),
        "sync-decisions.json": dump(sync_decisions()),
        "event-message.json": dump(event_message()),
        "metric-windows.json": dump(metric_windows()),
        "schema.sql": postgres_schema(),
    }


def main() -> int:
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in render().items():
        (CONTRACTS_DIR / name).write_text(content, encoding="utf-8", newline="\n")
        print(f"wrote {Path('packages/shared/contracts') / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
