"""Unit tests for the email parser (no network or database needed)."""
from __future__ import annotations

from pathlib import Path

from src.collection.email_parser import (
    canonical_newlines,
    clean_body,
    decode_mime_header,
    html_to_text,
    parse_address,
    parse_email_message,
    strip_quoted_reply,
    strip_signature,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --- Small unit helpers ---------------------------------------------------

def test_html_to_text_strips_markup_scripts_and_styles():
    html = (
        "<html><head><style>.x{color:red}</style></head><body>"
        "<p>Hello</p><script>bad()</script><p>World</p></body></html>"
    )
    text = html_to_text(html)
    assert "Hello" in text
    assert "World" in text
    assert "color:red" not in text
    assert "bad()" not in text
    assert "<" not in text


def test_decode_mime_header_q_encoding():
    assert decode_mime_header("=?UTF-8?Q?Weekly_Update_=E2=9C=93?=") == "Weekly Update ✓"


def test_decode_mime_header_plain_and_none():
    assert decode_mime_header("Just a subject") == "Just a subject"
    assert decode_mime_header(None) == ""


def test_parse_address_name_and_lowercased_email():
    name, addr = parse_address("Professor Ada Lovelace <ada@University.edu>")
    assert name == "Professor Ada Lovelace"
    assert addr == "ada@university.edu"
    assert parse_address(None) == (None, None)


def test_strip_quoted_reply_on_wrote_block():
    body = (
        "My new reply text.\n\n"
        "On Mon, Jul 21, 2025 at 3:00 PM Alice <alice@x.com> wrote:\n"
        "> old text\n> more old text\n"
    )
    assert strip_quoted_reply(body).strip() == "My new reply text."


def test_strip_quoted_reply_gt_quotes():
    body = "Answer above.\n> quoted line 1\n> quoted line 2\n"
    assert strip_quoted_reply(body).strip() == "Answer above."


def test_strip_signature_after_delimiter():
    body = "Message body here.\n\n-- \nJane Doe\nCEO, Example"
    assert strip_signature(body).strip() == "Message body here."


def test_clean_body_pipeline_quote_then_signature():
    body = (
        "Thanks Alice. I will send the status update by Friday.\n\n"
        "-- \nBob Smith\nSenior Engineer\n\n"
        "On Mon, Jul 21, 2025 at 3:00 PM Alice <alice@x.com> wrote:\n"
        "> Hi Bob, can you send me the status update this week?\n"
    )
    assert clean_body(body) == "Thanks Alice. I will send the status update by Friday."


# --- CRLF, which is what real mail actually looks like ---------------------
#
# RFC 5322 messages are CRLF-terminated, but every test above is written with LF
# because that is what a Python string literal gives you. The signature rule was
# anchored with `$` and a `[ \t]*` class that did not include `\r`, so it matched
# nothing on a real message and quietly passed the sender's job title and company
# to the model as though it were part of the email. It took running the suite
# against a fresh clone — where git had checked the fixture out as CRLF — for any
# test to notice.

def test_strip_signature_handles_crlf():
    body = "Message body here.\r\n\r\n-- \r\nJane Doe\r\nCEO, Example"
    assert strip_signature(body).strip() == "Message body here."


def test_clean_body_handles_a_crlf_message_end_to_end():
    body = (
        "Thanks Alice. I will send the status update by Friday.\r\n\r\n"
        "-- \r\nBob Smith\r\nSenior Engineer\r\n\r\n"
        "On Mon, Jul 21, 2025 at 3:00 PM Alice <alice@x.com> wrote:\r\n"
        "> Hi Bob, can you send me the status update this week?\r\n"
    )
    cleaned = clean_body(body)

    assert cleaned == "Thanks Alice. I will send the status update by Friday."
    assert "Senior Engineer" not in cleaned
    assert "\r" not in cleaned


def test_a_bare_delimiter_with_no_trailing_space_still_counts():
    """Many clients emit "--" without the RFC's trailing space."""
    assert strip_signature("Body.\r\n--\r\nJane").strip() == "Body."
    assert strip_signature("Body.\n--\nJane").strip() == "Body."


def test_canonical_newlines_collapses_every_ending():
    assert canonical_newlines("a\r\nb\rc\nd") == "a\nb\nc\nd"


# --- End-to-end fixture parsing ------------------------------------------

def test_parse_plain_email_fixture():
    parsed = parse_email_message(_read("plain_deadline.eml"))
    assert parsed.message_id == "plain-001@example.com"
    assert parsed.sender_name == "Professor Ada Lovelace"
    assert parsed.sender_email == "ada@university.edu"
    assert parsed.recipient_email == "student@example.com"
    assert parsed.subject == "Final project report"
    assert "15th August" in parsed.body_text
    assert parsed.received_at is not None
    assert (parsed.received_at.year, parsed.received_at.month) == (2025, 7)
    # A reply-less email threads under its own Message-ID.
    assert parsed.thread_id == "plain-001@example.com"


def test_parse_html_email_fixture():
    parsed = parse_email_message(_read("html_newsletter.eml"))
    assert parsed.subject == "Weekly Update ✓"
    assert "Friday" in parsed.body_text
    assert "Thanks for reading" in parsed.body_text
    assert "alert" not in parsed.body_text
    assert "color:red" not in parsed.body_text
    assert "<" not in parsed.body_text


def test_parse_threaded_reply_fixture():
    parsed = parse_email_message(_read("threaded_reply.eml"))
    # thread id comes from the References root, not this email's own id.
    assert parsed.thread_id == "root-000@example.com"
    assert parsed.sender_email == "bob@example.com"
    assert parsed.recipient_email == "alice@x.com"
    assert parsed.body_text == "Thanks Alice. I will send the status update by Friday."
    assert "wrote:" not in parsed.body_text
    assert "Senior Engineer" not in parsed.body_text
