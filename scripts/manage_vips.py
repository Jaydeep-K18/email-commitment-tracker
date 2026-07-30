"""Manage the VIP contact list from the command line.

A stopgap until the Phase 6 dashboard provides a UI (PROJECT_PLAN.md Phase 2).

    python -m scripts.manage_vips list
    python -m scripts.manage_vips add --value prof@university.edu --tier CRITICAL
    python -m scripts.manage_vips add --value university.edu --tier IMPORTANT
    python -m scripts.manage_vips add --value "Prof Ada" --tier CRITICAL
    python -m scripts.manage_vips remove --id 3
    python -m scripts.manage_vips retag            # re-tag stored emails
"""
from __future__ import annotations

import argparse

from src.filtering import vip_filter
from src.storage.database import (
    add_vip_contact,
    delete_vip_contact,
    init_db,
    list_vip_contacts,
    session_scope,
)


def infer_match_type(value: str) -> str:
    """Guess the rule kind from the value so ``--type`` is usually optional.

    ``ada@uni.edu`` -> exact_email, ``uni.edu`` -> domain, ``Prof Ada`` -> name_pattern.
    """
    candidate = value.strip()
    if "@" in candidate:
        return vip_filter.MATCH_EXACT_EMAIL
    if "." in candidate and " " not in candidate and "*" not in candidate:
        return vip_filter.MATCH_DOMAIN
    return vip_filter.MATCH_NAME_PATTERN


def cmd_list(_args: argparse.Namespace) -> int:
    with session_scope() as session:
        contacts = list_vip_contacts(session)
        if not contacts:
            print("No VIP contacts yet. Every sender will be tagged SKIP.")
            print(
                'Add one:  python -m scripts.manage_vips add '
                '--value someone@example.com --tier CRITICAL'
            )
            return 0
        print(f"{len(contacts)} VIP rule(s):\n")
        print(f"{'ID':<4} {'TIER':<10} {'MATCH TYPE':<13} {'VALUE':<32} NAME")
        print("-" * 78)
        for c in contacts:
            print(
                f"{c.id:<4} {c.tier:<10} {c.match_type:<13} "
                f"{c.match_value:<32} {c.display_name or ''}"
            )
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    match_type = args.type or infer_match_type(args.value)
    try:
        with session_scope() as session:
            contact = add_vip_contact(
                session,
                match_value=args.value,
                match_type=match_type,
                tier=args.tier,
                display_name=args.name,
            )
            contact_id, value, ctype, tier = (
                contact.id, contact.match_value, contact.match_type, contact.tier
            )
    except vip_filter.VipFilterError as exc:
        print(f"Error: {exc}")
        return 1

    print(f"Saved rule [{contact_id}]: {ctype} '{value}' -> {tier}")
    print("Apply it to already-stored emails with:  python -m scripts.manage_vips retag")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    with session_scope() as session:
        removed = delete_vip_contact(session, args.id)
    if not removed:
        print(f"No VIP rule with id {args.id}.")
        return 1
    print(f"Removed VIP rule {args.id}.")
    return 0


def cmd_retag(args: argparse.Namespace) -> int:
    with session_scope() as session:
        counts = vip_filter.apply_tiers_to_stored_emails(
            session, retag_all=args.all
        )
    total = counts.pop("total", 0)
    if total == 0:
        print("No emails needed tagging.")
        return 0
    summary = ", ".join(f"{tier}={n}" for tier, n in counts.items() if n)
    print(f"Tagged {total} email(s): {summary}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage_vips", description="Manage VIP contacts for the email tracker."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all VIP rules").set_defaults(func=cmd_list)

    add = sub.add_parser("add", help="Add or update a VIP rule")
    add.add_argument(
        "--value", required=True, help="Email address, domain, or name pattern"
    )
    add.add_argument(
        "--tier",
        required=True,
        help=f"One of: {', '.join(vip_filter.TIERS)}",
    )
    add.add_argument(
        "--type",
        choices=vip_filter.MATCH_TYPES,
        help="Match type (inferred from --value when omitted)",
    )
    add.add_argument("--name", help="Friendly display name")
    add.set_defaults(func=cmd_add)

    remove = sub.add_parser("remove", help="Remove a VIP rule by id")
    remove.add_argument("--id", type=int, required=True)
    remove.set_defaults(func=cmd_remove)

    retag = sub.add_parser("retag", help="Apply VIP tiers to stored emails")
    retag.add_argument(
        "--all",
        action="store_true",
        help="Re-evaluate every email, not just untagged ones",
    )
    retag.set_defaults(func=cmd_retag)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    init_db()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
