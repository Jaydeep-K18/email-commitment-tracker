"""Read *which* emails the user replied to, and when, from the Sent folder.

Response-time metrics need two timestamps: when an email arrived, and when the
user answered it. The second lives only in the Sent folder, matched back to the
original through the reply's ``In-Reply-To`` header.

Only four headers are fetched — Message-ID, In-Reply-To, To and Date — using
``BODY.PEEK[HEADER.FIELDS ...]``. The body of anything the user sent is never
downloaded, let alone stored: the metric needs none of it, and an app that reads
your inbox has no business also keeping a copy of your outgoing mail. The
mailbox is selected read-only, same as the inbox.
"""
from __future__ import annotations

import imaplib
import logging
import re
from dataclasses import dataclass
from email import message_from_bytes

from sqlalchemy import select
from sqlalchemy.orm import Session

from src import config
from src.collection.email_fetcher import _since_criterion, connect
from src.collection.email_parser import (
    _clean_message_id,
    _first_message_id,
    _parse_date,
    address_list,
)
from src.storage.database import session_scope
from src.storage.models import SentMessage

log = logging.getLogger(__name__)

#: Folder names used when the server does not advertise the RFC 6154 \Sent
#: flag. Gmail, Outlook/Exchange and Apple Mail respectively.
_FALLBACK_SENT_NAMES = ("[Gmail]/Sent Mail", "Sent Items", "Sent Messages", "Sent")

#: One line of an IMAP LIST response: (flags) "delimiter" name
_LIST_LINE = re.compile(r'\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.+)$')

_HEADERS = "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO TO DATE)])"


@dataclass(frozen=True)
class SentHeaders:
    message_id: str
    in_reply_to: str | None
    recipient_email: str | None
    sent_at: object  # datetime | None, naive UTC


def parse_list_line(line: bytes | str) -> tuple[set[str], str] | None:
    """``(flags, mailbox name)`` from one LIST line, or None if unparseable."""
    text = line.decode(errors="replace") if isinstance(line, bytes) else line
    match = _LIST_LINE.match(text.strip())
    if not match:
        return None
    flags = {flag.lower() for flag in match.group("flags").split()}
    name = match.group("name").strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    return flags, name


def find_sent_mailbox(imap: imaplib.IMAP4) -> str | None:
    """The Sent folder's name, by its special-use flag where the server sets one.

    The flag is preferred over guessing names because the folder's name is
    localised — it is "Gesendet" on a German Gmail account.
    """
    typ, data = imap.list()
    if typ != "OK" or not data:
        return None
    names: list[str] = []
    for line in data:
        if line is None:
            continue
        parsed = parse_list_line(line)
        if parsed is None:
            continue
        flags, name = parsed
        if "\\sent" in flags:
            return name
        names.append(name)
    for candidate in _FALLBACK_SENT_NAMES:
        if candidate in names:
            return candidate
    return None


def parse_sent_headers(raw: bytes) -> SentHeaders | None:
    msg = message_from_bytes(raw)
    message_id = _clean_message_id(msg.get("Message-ID"))
    if not message_id:
        return None
    to = address_list(msg.get("To"))
    return SentHeaders(
        message_id=message_id,
        in_reply_to=_first_message_id(msg.get("In-Reply-To")),
        recipient_email=to.split(", ")[0] if to else None,
        sent_at=_parse_date(msg.get("Date")),
    )


def fetch_sent_headers(imap: imaplib.IMAP4, mailbox: str) -> list[SentHeaders]:
    """Headers of recently sent messages, in one round trip."""
    typ, _ = imap.select(f'"{mailbox}"', readonly=True)
    if typ != "OK":
        return []
    typ, data = imap.search(None, "SINCE", _since_criterion(config.FETCH_LOOKBACK_DAYS))
    if typ != "OK" or not data or not data[0]:
        return []
    ids = data[0].split()
    if config.FETCH_MAX_EMAILS > 0:
        ids = ids[-config.FETCH_MAX_EMAILS:]

    typ, payload = imap.fetch(b",".join(ids), _HEADERS)
    if typ != "OK" or not payload:
        return []
    found = []
    for part in payload:
        if isinstance(part, tuple) and part[1]:
            headers = parse_sent_headers(part[1])
            if headers is not None:
                found.append(headers)
    return found


def store_sent(session: Session, items: list[SentHeaders]) -> int:
    """Insert the ones not already stored. Returns how many were new."""
    if not items:
        return 0
    known = set(
        session.scalars(
            select(SentMessage.message_id).where(
                SentMessage.message_id.in_([item.message_id for item in items])
            )
        )
    )
    new = [item for item in items if item.message_id not in known]
    session.add_all(
        SentMessage(
            message_id=item.message_id,
            in_reply_to=item.in_reply_to,
            recipient_email=item.recipient_email,
            sent_at=item.sent_at,
        )
        for item in new
    )
    session.flush()
    return len(new)


def sync_sent_messages() -> int:
    """Pull recent Sent headers over IMAP. Never raises — the metric is optional.

    A mailbox without IMAP configured (a Gmail-API-only setup) simply has no
    response-time data; that is reported as zero, not as a failed cycle.
    """
    if not config.IMAP_USER:
        return 0
    try:
        imap = connect()
    except Exception as exc:  # noqa: BLE001 - optional data, never fatal
        log.info("Sent folder skipped: IMAP unavailable (%s)", exc)
        return 0
    try:
        mailbox = find_sent_mailbox(imap)
        if mailbox is None:
            log.info("No Sent folder found; response times will be unavailable.")
            return 0
        items = fetch_sent_headers(imap, mailbox)
        with session_scope() as session:
            return store_sent(session, items)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read the Sent folder: %s", exc)
        return 0
    finally:
        try:
            imap.logout()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
