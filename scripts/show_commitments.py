"""Print extracted commitments with their source evidence.

    python -m scripts.show_commitments
    python -m scripts.show_commitments --type deadline_on_you
"""
from __future__ import annotations

import argparse

from src.extraction.schemas import CommitmentType
from src.storage.database import init_db, list_commitments, session_scope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="show_commitments")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--type", choices=[t.value for t in CommitmentType], help="Filter by type"
    )
    parser.add_argument("--status", help="Filter by status (default: any)")
    args = parser.parse_args(argv)

    init_db()
    with session_scope() as session:
        rows = list_commitments(
            session,
            limit=args.limit,
            commitment_type=args.type,
            status=args.status,
        )
        if not rows:
            print("No commitments yet. Run:  python -m scripts.run_extraction")
            return 0

        print(f"{len(rows)} commitment(s):\n")
        for row in rows:
            due = row.deadline.strftime("%Y-%m-%d %H:%M") if row.deadline else "no date"
            print(f"[{row.id}] {row.type}  (confidence {row.confidence:.2f})")
            print(f"    What    : {row.subject}")
            print(f"    Due     : {due}")
            print(f"    With    : {row.counterparty_name or '?'} "
                  f"<{row.counterparty_email or '?'}>")
            print(f"    Tier    : {row.vip_tier or '-'}   Status: {row.status}")
            print(f"    Evidence: \"{row.evidence_quote}\"")
            print(f"    Source  : email #{row.email_id}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
