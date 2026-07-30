"""Run the LLM extraction pipeline over VIP-filtered emails.

    python -m scripts.run_extraction              # process everything pending
    python -m scripts.run_extraction --limit 5    # just the newest 5
    python -m scripts.run_extraction --limit 1 --verbose

Inference is local and CPU-bound: expect roughly 10-60 seconds per email.
"""
from __future__ import annotations

import argparse
import logging

from src.extraction.ollama_client import OllamaClient, OllamaError
from src.extraction.pipeline import process_pending_emails
from src.storage.database import (
    init_db,
    reset_emails_for_reprocessing,
    session_scope,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_extraction", description="Extract commitments from stored emails."
    )
    parser.add_argument(
        "--limit", type=int, help="Maximum number of emails to process"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing commitments for an email before re-extracting",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Re-queue already-processed emails (use after changing the prompt)",
    )
    parser.add_argument("--verbose", action="store_true", help="Show debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    init_db()

    if args.reprocess:
        with session_scope() as session:
            requeued = reset_emails_for_reprocessing(session, limit=args.limit)
        print(f"Re-queued {requeued} already-processed email(s).")
        if requeued and not args.replace:
            print(
                "Tip: add --replace to remove their old commitments first, "
                "otherwise you will get duplicates."
            )

    client = OllamaClient()
    try:
        client.ensure_ready()
    except OllamaError as exc:
        print(f"Ollama not ready: {exc}")
        return 1

    print(f"Model: {client.model}  (local inference, nothing leaves this machine)\n")

    try:
        stats = process_pending_emails(
            limit=args.limit, client=client, replace_existing=args.replace
        )
    except OllamaError as exc:
        print(f"\nExtraction stopped: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. Processed emails are saved; the rest stay queued.")
        return 130

    if stats.emails_processed == 0 and stats.emails_failed == 0:
        print("No emails awaiting extraction.")
        print(
            "Emails must have a non-SKIP VIP tier. Add a contact with:\n"
            "  python -m scripts.manage_vips add --value someone@example.com "
            "--tier CRITICAL\n"
            "  python -m scripts.manage_vips retag"
        )
        return 0

    print(
        f"\nProcessed {stats.emails_processed} email(s) in "
        f"{stats.duration_seconds:.0f}s — stored {stats.commitments_stored} "
        f"commitment(s)."
    )
    if stats.by_type:
        for name, count in sorted(stats.by_type.items()):
            print(f"  {name}: {count}")
    if stats.retries:
        print(f"  (validation retries: {stats.retries})")
    if stats.discarded:
        print(
            f"  (discarded {stats.discarded} untrustworthy extraction(s): "
            "bad evidence, boilerplate, or low confidence)"
        )
    if stats.emails_failed:
        print(f"  failed: {stats.emails_failed} (left queued for the next run)")

    print("\nView them with:  python -m scripts.show_commitments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
