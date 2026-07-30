"""Review commitments the sync engine is holding back from the calendar.

MONITOR-tier and untiered commitments never reach the calendar on their own —
the user decides. This is the command-line stand-in until the Phase 6 dashboard
provides the same thing with buttons.

    python -m scripts.review_queue                 # list what is waiting
    python -m scripts.review_queue approve 7       # put #7 on the calendar
    python -m scripts.review_queue approve --all
    python -m scripts.review_queue dismiss 7       # never show it again
    python -m scripts.review_queue revoke 7        # undo an approval

Approving only marks the commitment; run ``python -m scripts.publish_calendar``
(or let the server regenerate the feed) to see it appear.
"""
from __future__ import annotations

import argparse

from src.storage.database import (
    init_db,
    session_scope,
    set_commitment_status,
    set_sync_approval,
)
from src.sync.sync_engine import decide, review_queue


def _format(commitment) -> str:
    when = (
        commitment.deadline.strftime("%a %d %b %Y %H:%M")
        if commitment.deadline
        else "no deadline"
    )
    tier = commitment.vip_tier or "untiered"
    who = commitment.counterparty_name or commitment.counterparty_email or "unknown"
    return (
        f"  #{commitment.id}  [{tier}]  {when}\n"
        f"      {commitment.subject}\n"
        f"      from {who} — {decide(commitment).reason}\n"
        f'      evidence: "{(commitment.evidence_quote or "").strip()[:100]}"'
    )


def cmd_list(_args) -> int:
    with session_scope() as session:
        pending = review_queue(session)
        lines = [_format(c) for c in pending]

    if not lines:
        print("Nothing waiting for review.")
        return 0
    print(f"{len(lines)} commitment(s) waiting for your approval:\n")
    print("\n\n".join(lines))
    print("\nApprove with:  python -m scripts.review_queue approve <id>")
    return 0


def cmd_approve(args) -> int:
    with session_scope() as session:
        if args.all:
            targets = [c.id for c in review_queue(session)]
        else:
            targets = args.ids
        if not targets:
            print("Nothing to approve.")
            return 0
        approved = []
        for commitment_id in targets:
            commitment = set_sync_approval(session, commitment_id, True)
            if commitment is None:
                print(f"No commitment with id {commitment_id}.")
                continue
            approved.append(f"#{commitment.id} {commitment.subject}")

    for line in approved:
        print(f"Approved {line}")
    if approved:
        print("\nPublish with:  python -m scripts.publish_calendar")
    return 0


def cmd_revoke(args) -> int:
    with session_scope() as session:
        for commitment_id in args.ids:
            commitment = set_sync_approval(session, commitment_id, False)
            print(
                f"No commitment with id {commitment_id}."
                if commitment is None
                else f"Revoked approval for #{commitment.id} {commitment.subject}"
            )
    return 0


def cmd_dismiss(args) -> int:
    with session_scope() as session:
        for commitment_id in args.ids:
            commitment = set_commitment_status(session, commitment_id, "dismissed")
            print(
                f"No commitment with id {commitment_id}."
                if commitment is None
                else f"Dismissed #{commitment.id} {commitment.subject}"
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review_queue", description=__doc__.splitlines()[0]
    )
    subparsers = parser.add_subparsers(dest="command")

    listing = subparsers.add_parser("list", help="show commitments awaiting approval")
    listing.set_defaults(func=cmd_list)

    approve = subparsers.add_parser("approve", help="allow commitments on the calendar")
    approve.add_argument("ids", nargs="*", type=int, help="commitment ids")
    approve.add_argument("--all", action="store_true", help="approve everything queued")
    approve.set_defaults(func=cmd_approve)

    revoke = subparsers.add_parser("revoke", help="undo an approval")
    revoke.add_argument("ids", nargs="+", type=int, help="commitment ids")
    revoke.set_defaults(func=cmd_revoke)

    dismiss = subparsers.add_parser("dismiss", help="drop commitments permanently")
    dismiss.add_argument("ids", nargs="+", type=int, help="commitment ids")
    dismiss.set_defaults(func=cmd_dismiss)

    parser.set_defaults(func=cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    init_db()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
