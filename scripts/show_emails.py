"""Print the most recently stored emails for quick inspection.

    python -m scripts.show_emails [N]      (default N=10)
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from src.storage.database import init_db, session_scope
from src.storage.models import RawEmail


def main(limit: int = 10) -> int:
    init_db()
    with session_scope() as session:
        rows = (
            session.execute(
                select(RawEmail)
                .order_by(RawEmail.received_at.desc(), RawEmail.id.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        if not rows:
            print("No emails stored yet. Run: python -m src.collection.email_fetcher")
            return 0

        print(f"Showing {len(rows)} most recent email(s):\n")
        for row in rows:
            received = (
                row.received_at.strftime("%Y-%m-%d %H:%M") if row.received_at else "?"
            )
            body = row.body_text or ""
            preview = " ".join(body.split())[:100]
            more = "..." if len(body) > 100 else ""
            tier = row.vip_tier or "untagged"
            flag = "processed" if row.processed else "pending"
            print(f"[{row.id}] {received}  {row.sender_email or '?'}")
            print(f"    Subject: {row.subject or '(none)'}")
            print(f"    Tier   : {tier} ({flag})")
            print(f"    Thread : {row.thread_id or '-'}")
            print(f"    Body   : {preview}{more}")
            print()
    return 0


if __name__ == "__main__":
    count = 10
    if len(sys.argv) > 1:
        try:
            count = int(sys.argv[1])
        except ValueError:
            pass
    raise SystemExit(main(count))
