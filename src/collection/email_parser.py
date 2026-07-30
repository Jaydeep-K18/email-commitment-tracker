"""Email parsing: raw RFC822 bytes -> a clean, structured :class:`ParsedEmail`.

Uses only the stdlib ``email`` package plus BeautifulSoup (per the plan's tech
stack, §8). It produces a plain-text body with quoted replies and signatures
stripped, decoded headers, parsed addresses/dates, and a best-effort thread id.

This module is storage-agnostic: it imports nothing from the storage layer, so it
stays easy to unit-test in isolation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

from bs4 import BeautifulSoup


@dataclass
class ParsedEmail:
    """Structured, cleaned representation of one email."""

    message_id: str
    thread_id: str | None
    sender_name: str | None
    sender_email: str | None
    recipient_email: str | None
    subject: str | None
    body_text: str
    received_at: datetime | None


# --- Header helpers -------------------------------------------------------

def decode_mime_header(raw: str | None) -> str:
    """Decode an RFC 2047 header (e.g. ``=?UTF-8?B?...?=``) into plain text."""
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:
        return raw.strip()


def parse_address(raw: str | None) -> tuple[str | None, str | None]:
    """Return ``(display_name, email_address)`` from a From/To header value."""
    if not raw:
        return None, None
    name, addr = parseaddr(decode_mime_header(raw))
    return (name.strip() or None), (addr.strip().lower() or None)


def _clean_message_id(value: str | None) -> str:
    """Extract the bare Message-ID (drop the surrounding angle brackets)."""
    if not value:
        return ""
    match = re.search(r"<([^>]+)>", value)
    return (match.group(1) if match else value).strip().strip("<>")


def _first_message_id(value: str | None) -> str | None:
    """Return the first ``<id>`` in a References/In-Reply-To header, if any."""
    if not value:
        return None
    ids = re.findall(r"<([^>]+)>", value)
    if ids:
        return ids[0].strip()
    stripped = value.strip().strip("<>")
    return stripped or None


def derive_thread_id(msg: Message, message_id: str) -> str | None:
    """Best-effort thread id: References root, else In-Reply-To, else self.

    The first id in ``References`` is the thread's root message; a reply with no
    references starts its own thread (thread id == its own Message-ID).
    """
    root = _first_message_id(msg.get("References")) or _first_message_id(
        msg.get("In-Reply-To")
    )
    return root or (message_id or None)


def _parse_date(value: str | None) -> datetime | None:
    """Parse a Date header to a naive-UTC datetime (matching storage)."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# --- Body extraction ------------------------------------------------------

def html_to_text(html: str) -> str:
    """Extract readable plain text from an HTML email body."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head", "title", "meta"]):
        tag.decompose()
    return soup.get_text("\n")


def _decode_part(part: Message) -> str:
    """Decode a single MIME part's payload to text using its charset."""
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, TypeError):
        return payload.decode("utf-8", errors="replace")


def get_body(msg: Message) -> str:
    """Return the best plain-text body, preferring ``text/plain`` over HTML."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            disposition = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                plain_parts.append(_decode_part(part))
            elif content_type == "text/html":
                html_parts.append(_decode_part(part))
    else:
        if msg.get_content_type() == "text/html":
            html_parts.append(_decode_part(msg))
        else:
            plain_parts.append(_decode_part(msg))

    if any(p.strip() for p in plain_parts):
        return "\n".join(p for p in plain_parts if p.strip())
    if html_parts:
        return html_to_text("\n".join(html_parts))
    return ""


# --- Cleaning: quoted replies, signatures, whitespace ---------------------

# Ordered heuristics that mark where quoted/forwarded content begins. We cut the
# body at the earliest match. Conservative by design — better to keep a little
# extra than to drop the user's own text.
_QUOTE_PATTERNS = [
    # Gmail / Apple Mail: "On <date incl. a 4-digit year> ... wrote:"
    re.compile(r"^On\b.{0,400}?\d{4}.{0,200}?wrote:", re.MULTILINE | re.DOTALL),
    # Outlook: "-----Original Message-----"
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.MULTILINE | re.IGNORECASE),
    # Outlook underscore divider before a quoted header block
    re.compile(r"^_{5,}", re.MULTILINE),
    # Any run of quoted lines beginning with ">"
    re.compile(r"^\s*>", re.MULTILINE),
]

# RFC 3676 signature delimiter: a line containing "-- " (trailing space optional).
_SIGNATURE_PATTERN = re.compile(r"^--[ \t]*$", re.MULTILINE)


def strip_quoted_reply(text: str) -> str:
    """Remove quoted/forwarded content, keeping only the new message on top."""
    cut = len(text)
    for pattern in _QUOTE_PATTERNS:
        match = pattern.search(text)
        if match and match.start() < cut:
            cut = match.start()
    return text[:cut].rstrip()


def strip_signature(text: str) -> str:
    """Remove a trailing signature block delimited by the ``-- `` line."""
    match = _SIGNATURE_PATTERN.search(text)
    if match:
        return text[: match.start()].rstrip()
    return text


def normalize_whitespace(text: str) -> str:
    """Trim trailing spaces, collapse 3+ blank lines, and strip the ends."""
    lines = [line.rstrip() for line in text.splitlines()]
    collapsed = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return collapsed.strip()


def clean_body(text: str) -> str:
    """Full body cleanup pipeline: drop quotes, then signature, then normalise."""
    text = strip_quoted_reply(text)
    text = strip_signature(text)
    return normalize_whitespace(text)


# --- Public entry point ---------------------------------------------------

def parse_email_message(raw_bytes: bytes) -> ParsedEmail:
    """Parse raw RFC822 bytes into a cleaned :class:`ParsedEmail`."""
    msg = message_from_bytes(raw_bytes)

    message_id = _clean_message_id(msg.get("Message-ID"))
    sender_name, sender_email = parse_address(msg.get("From"))
    _, recipient_email = parse_address(msg.get("To"))

    return ParsedEmail(
        message_id=message_id,
        thread_id=derive_thread_id(msg, message_id),
        sender_name=sender_name,
        sender_email=sender_email,
        recipient_email=recipient_email,
        subject=decode_mime_header(msg.get("Subject")) or None,
        body_text=clean_body(get_body(msg)),
        received_at=_parse_date(msg.get("Date")),
    )
