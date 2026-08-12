"""IMAP email fetching.

Connects to the configured inbox, pulls recent messages *non-intrusively* (a
read-only mailbox SELECT plus ``BODY.PEEK[]`` so the user's unread flags are never
touched), parses them, and stores new ones. Dedup is by Message-ID, so re-running
a fetch is idempotent.

Run one cycle manually:

    python -m src.collection.email_fetcher
"""
from __future__ import annotations

import imaplib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src import config
from src.collection.email_parser import parse_email_message
from src.filtering import vip_filter
from src.storage.database import init_db, save_email, session_scope

log = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Summary of one fetch cycle."""

    fetched: int = 0
    new: int = 0
    duplicates: int = 0
    subjects: list[str] = field(default_factory=list)
    #: Count of newly stored emails per VIP tier (Phase 2).
    by_tier: dict[str, int] = field(
        default_factory=lambda: {tier: 0 for tier in vip_filter.TIERS}
    )


def connect() -> imaplib.IMAP4:
    """Open and authenticate an IMAP connection using keyring credentials."""
    password = config.get_imap_password()
    if config.IMAP_USE_SSL:
        imap: imaplib.IMAP4 = imaplib.IMAP4_SSL(config.IMAP_HOST, config.IMAP_PORT)
    else:
        imap = imaplib.IMAP4(config.IMAP_HOST, config.IMAP_PORT)
    imap.login(config.IMAP_USER, password)
    return imap


def _since_criterion(lookback_days: int) -> str:
    """IMAP SINCE date string, e.g. ``22-Jul-2025``."""
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    return since.strftime("%d-%b-%Y")


def fetch_recent(imap: imaplib.IMAP4) -> list[bytes]:
    """Return raw RFC822 bytes for recent messages (newest last).

    Read-only SELECT + ``BODY.PEEK[]`` guarantee we never set the ``\\Seen`` flag.
    """
    imap.select(config.IMAP_MAILBOX, readonly=True)
    typ, data = imap.search(
        None, "SINCE", _since_criterion(config.FETCH_LOOKBACK_DAYS)
    )
    if typ != "OK" or not data or not data[0]:
        return []

    ids = data[0].split()
    if config.FETCH_MAX_EMAILS > 0:
        ids = ids[-config.FETCH_MAX_EMAILS :]

    raws: list[bytes] = []
    for msg_id in ids:
        typ, msg_data = imap.fetch(msg_id, "(BODY.PEEK[])")
        if typ != "OK" or not msg_data:
            log.warning("Failed to fetch message %s", msg_id.decode(errors="replace"))
            continue
        for part in msg_data:
            if isinstance(part, tuple) and part[1]:
                raws.append(part[1])
                break
    return raws


def collect_raw_messages() -> list[bytes]:
    """Fetch raw messages from whichever transport is configured.

    Google is preferred when signed in, because that is the path the user chose
    on the setup screen. IMAP remains the fallback and is the only route for
    non-Gmail mailboxes, so neither can be removed.

    Both return raw RFC822 bytes, which is what keeps parsing, VIP filtering and
    dedup identical regardless of how the mail arrived.
    """
    from src.collection import gmail_fetcher

    if gmail_fetcher.is_available():
        try:
            return gmail_fetcher.fetch_recent()
        except Exception as exc:  # noqa: BLE001
            # A Google outage or a revoked token should not strand a user who
            # still has a working app password configured.
            log.warning("Gmail API fetch failed (%s); trying IMAP.", exc)

    imap = connect()
    try:
        return fetch_recent(imap)
    finally:
        try:
            imap.logout()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


def fetch_and_store() -> FetchResult:
    """Fetch recent emails and store any new ones. Returns a :class:`FetchResult`."""
    init_db()
    raws = collect_raw_messages()

    # Load VIP rules once for the whole batch rather than per email.
    with session_scope() as session:
        rules = vip_filter.load_rules(session)

    result = FetchResult(fetched=len(raws))
    for raw in raws:
        try:
            parsed = parse_email_message(raw)
        except Exception as exc:  # one bad message must not abort the batch
            log.warning("Parse failed, skipping one message: %s", exc)
            continue
        if not parsed.message_id:
            log.warning("Message with no Message-ID, skipping")
            continue

        decision = vip_filter.assign_tier(
            rules, parsed.sender_email, parsed.sender_name
        )
        try:
            with session_scope() as session:
                saved = save_email(
                    session,
                    parsed,
                    vip_tier=decision.tier,
                    # SKIP senders are closed out immediately — never extracted.
                    processed=not decision.should_process,
                )
            if saved is None:
                result.duplicates += 1
            else:
                result.new += 1
                result.by_tier[decision.tier] = (
                    result.by_tier.get(decision.tier, 0) + 1
                )
                if decision.should_process:
                    result.subjects.append(
                        f"[{decision.tier}] {parsed.subject or '(no subject)'}"
                    )
        except Exception as exc:
            log.warning("Store failed for %s: %s", parsed.message_id, exc)

    log.info(
        "Fetch complete: %d fetched, %d new, %d duplicates, tiers=%s",
        result.fetched, result.new, result.duplicates, result.by_tier,
    )
    return result


def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_db()
    try:
        result = fetch_and_store()
    except config.ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 1
    except imaplib.IMAP4.error as exc:
        print(
            f"IMAP error: {exc}\n"
            "Check IMAP_USER / IMAP_HOST in .env and that your app password is "
            "stored (python -m scripts.set_imap_password)."
        )
        return 1
    except OSError as exc:
        print(f"Network error connecting to {config.IMAP_HOST}: {exc}")
        return 1

    print(
        f"Stored {result.new} new emails "
        f"({result.duplicates} skipped as duplicates)."
    )

    tiers = {t: n for t, n in result.by_tier.items() if n}
    if tiers:
        print("  Tiers: " + ", ".join(f"{t}={n}" for t, n in tiers.items()))

    for subject in result.subjects[:5]:
        print(f"  - {subject}")

    with session_scope() as session:
        rule_count = len(vip_filter.load_rules(session))
    if rule_count == 0:
        print(
            "\nNote: no VIP contacts defined, so every sender is tagged SKIP.\n"
            "Add one with:  python -m scripts.manage_vips add "
            '--value someone@example.com --tier CRITICAL'
        )
    elif result.new:
        print("\nInspect them with:  python -m scripts.show_emails")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
