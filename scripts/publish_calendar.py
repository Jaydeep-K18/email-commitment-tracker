"""Run a sync cycle and write the .ics calendar file.

    python -m scripts.publish_calendar

Resolves duplicate commitments, applies the VIP tier policy, and writes
``data/calendar.ics``. The server regenerates the feed on demand, so this is
mainly for inspecting the file or importing it manually.
"""
from __future__ import annotations

from src import config
from src.storage.database import init_db, session_scope
from src.sync.sync_engine import run_sync


def main() -> int:
    init_db()
    with session_scope() as session:
        report = run_sync(session)

    if report.errors:
        print("Calendar publish FAILED:")
        for error in report.errors:
            print(f"  {error}")
        print(f"\n{report.failed} commitment(s) queued for retry on the next run.")
        return 1

    if report.superseded:
        print(f"Superseded {report.superseded} duplicate commitment(s).")

    if report.published == 0:
        print("Nothing is eligible for the calendar yet.")
        if report.awaiting_approval:
            print(
                f"  {report.awaiting_approval} commitment(s) waiting for approval:  "
                f"python -m scripts.review_queue"
            )
        else:
            print("  Run:  python -m scripts.run_extraction")
        return 0

    print(
        f"Published {report.published} event(s) "
        f"({report.created} new, {report.updated} updated) "
        f"to {report.path} ({report.bytes_written} bytes)."
    )
    if report.flagged:
        print(f"  {report.flagged} flagged for review (IMPORTANT tier).")
    if report.awaiting_approval:
        print(
            f"  {report.awaiting_approval} held for approval:  "
            f"python -m scripts.review_queue"
        )
    if report.retried:
        print(f"  {report.retried} retried after an earlier failure.")
    print(
        f"\nServe it with:  python -m src.server.calendar_server\n"
        f"Then subscribe to: "
        f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/calendar.ics"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
