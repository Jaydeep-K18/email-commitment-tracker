"""Generate the .ics calendar file from stored commitments.

    python -m scripts.publish_calendar

Writes ``data/calendar.ics``. The server regenerates the feed on demand, so this
is mainly for inspecting the file or importing it manually.
"""
from __future__ import annotations

from src import config
from src.storage.database import init_db, recent_sync_log, session_scope
from src.sync.ics_builder import publish_calendar


def main() -> int:
    init_db()
    with session_scope() as session:
        result = publish_calendar(session)
        log_entries = recent_sync_log(session, limit=5)
        recent = [(e.action, e.status) for e in log_entries]

    if result.events == 0:
        print("No commitments with a deadline yet — nothing to publish.")
        print("Run:  python -m scripts.run_extraction")
        return 0

    print(
        f"Published {result.events} event(s) "
        f"({result.created} new, {result.updated} updated) "
        f"to {result.path} ({result.bytes_written} bytes)."
    )
    if recent:
        summary = ", ".join(f"{action}:{status}" for action, status in recent)
        print(f"  sync_log: {summary}")
    print(
        f"\nServe it with:  python -m src.server.calendar_server\n"
        f"Then subscribe to: "
        f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/calendar.ics"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
