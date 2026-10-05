"""Sort every email into one of five inbox categories, and say why.

Rules rather than the language model, deliberately:

* **Coverage.** The model only ever sees VIP-tier mail (that is what the filter
  is for), but every email in the inbox needs a category. Running a CPU-bound
  3B model over all of them would take tens of seconds per email.
* **Explainability.** Each rule produces a sentence the UI shows next to the
  category. "Sent to a mailing list" is something a user can check and disagree
  with; a confidence score from a model is not.
* **Stability.** The same email always lands in the same place.

The language model still contributes: once extraction has run, the commitments
it found are the strongest signal of all, and the email is re-classified with
them. A category the user picked by hand is never overwritten by either pass.

Rules are checked in order and the first that matches wins, so the order *is*
the policy: a calendar invite from a newsletter is still a meeting, and a
request from a VIP is action-required rather than merely important.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- Categories -----------------------------------------------------------

IMPORTANT = "important"
ACTION_REQUIRED = "action_required"
MEETING = "meeting"
UPDATE = "update"
LOW_PRIORITY = "low_priority"

CATEGORIES: tuple[str, ...] = (IMPORTANT, ACTION_REQUIRED, MEETING, UPDATE, LOW_PRIORITY)

#: Sort order for "by priority": what needs the user soonest comes first.
CATEGORY_RANK = {
    ACTION_REQUIRED: 0,
    MEETING: 1,
    IMPORTANT: 2,
    UPDATE: 3,
    LOW_PRIORITY: 4,
}

SOURCE_RULE = "rule"
SOURCE_LLM = "llm"
SOURCE_USER = "user"

# --- Signals --------------------------------------------------------------

#: Local parts used by machines and marketing teams rather than people.
_AUTOMATED_LOCAL = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|notifications?|notify|alerts?|"
    r"mailer-daemon|mailers?|postmaster|bounces?|automated|system|updates?|"
    r"news(letter)?|info|support|billing|receipts?|team|marketing|welcome|hello|"
    r"offers?|promos?|deals|recommendations?\w*|careers?|jobs|accounts?|admin|"
    r"orders?|dining|rewards|feedback)([+._-].*)?$",
    re.IGNORECASE,
)

#: Sending subdomains that bulk-mail platforms use ("email.example.com").
#: Matched as a whole label, so "gmail.com" is not mistaken for "mail.".
_AUTOMATED_DOMAIN = re.compile(
    r"(^|\.)(no-?reply|mailers?|marketing|news|email|mail|e|em|info|notify|"
    r"notifications|bounce)\.",
    re.IGNORECASE,
)

#: Domains where an address belongs to a person rather than an organisation.
_PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "yahoo.com", "yahoo.co.in", "ymail.com", "icloud.com", "me.com",
    "mac.com", "proton.me", "protonmail.com", "aol.com", "zoho.com", "gmx.com",
    "rediffmail.com",
}

#: University domains, whose addresses are individual students and staff:
#: .edu, .edu.<cc>, and .ac.<cc> (e.g. it.vjti.ac.in).
_ACADEMIC_DOMAIN = re.compile(r"(\.edu|\.edu\.[a-z]{2}|\.ac\.[a-z]{2})$", re.IGNORECASE)

#: Subjects that calendar software writes on invitations and their replies.
_INVITE_SUBJECT = re.compile(
    r"^(invitation|updated invitation|invitation from|accepted|declined|tentative)\b",
    re.IGNORECASE,
)

#: Wording that marks marketing and digests. Only consulted for mail that is
#: already bulk, so a colleague writing "50% off" is not demoted.
_PROMOTIONAL = re.compile(
    r"\b(unsubscribe|newsletter|digest|webinar|% off|sale|deals?|offer|promo(tion)?|"
    r"discount|coupon|limited time|shop now|free trial)\b",
    re.IGNORECASE,
)

#: Direct requests. Kept narrow on purpose: these promote an email above
#: everything except meetings, so a false positive is costly.
_REQUEST = re.compile(
    r"\b(action required|please (confirm|reply|respond|review|approve|sign|submit|send|"
    r"complete|fill)|rsvp|kindly (confirm|send|submit|review)|"
    r"can you (please )?(send|share|review|confirm|check)|"
    r"could you (please )?(send|share|review|confirm|check)|"
    r"due (by|on|before)|deadline)\b",
    re.IGNORECASE,
)

_MEETING_WORDS = re.compile(
    r"\b(invitation|invite[d]?|meeting|interview|call scheduled|calendar)\b",
    re.IGNORECASE,
)
_MEETING_LINKS = re.compile(
    r"(zoom\.us/j/|meet\.google\.com/|teams\.microsoft\.com/l/meetup|webex\.com/meet)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EmailSignals:
    """Everything the rules look at. Built from a RawEmail or a ParsedEmail."""

    subject: str = ""
    body_text: str = ""
    sender_email: str = ""
    vip_tier: str | None = None
    is_bulk: bool = False
    has_invite: bool = False
    is_reply: bool = False
    #: Commitment types the extraction pipeline found; empty before it runs.
    commitment_types: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_email(cls, email, commitment_types=()) -> "EmailSignals":  # noqa: ANN001
        return cls(
            subject=email.subject or "",
            body_text=email.body_text or "",
            sender_email=email.sender_email or "",
            vip_tier=getattr(email, "vip_tier", None),
            is_bulk=bool(getattr(email, "is_bulk", False)),
            has_invite=bool(getattr(email, "has_invite", False)),
            is_reply=bool(getattr(email, "in_reply_to", None)),
            commitment_types=tuple(commitment_types),
        )

    @property
    def text(self) -> str:
        return f"{self.subject}\n{self.body_text}"

    @property
    def _local_and_domain(self) -> tuple[str, str]:
        local, _, domain = self.sender_email.lower().partition("@")
        return local, domain

    @property
    def automated(self) -> bool:
        local, domain = self._local_and_domain
        return bool(_AUTOMATED_LOCAL.match(local) or _AUTOMATED_DOMAIN.search(domain))

    @property
    def from_a_person(self) -> bool:
        """An address that belongs to an individual, not an organisation."""
        _, domain = self._local_and_domain
        return domain in _PERSONAL_DOMAINS or bool(_ACADEMIC_DOMAIN.search(domain))

    @property
    def mass_mailed(self) -> bool:
        """Bulk by header, or — for mail stored before headers were kept — by
        the unsubscribe line every legitimate mailing list is required to carry."""
        return self.is_bulk or "unsubscribe" in self.body_text.lower()


@dataclass(frozen=True)
class Classification:
    category: str
    reason: str
    source: str = SOURCE_RULE


def classify(signals: EmailSignals) -> Classification:
    """Place one email. Pure: no database, no clock, no randomness."""
    found = set(signals.commitment_types)

    # 1. A real invitation attachment is unambiguous.
    if signals.has_invite:
        return Classification(MEETING, "Contains a calendar invitation.")

    # 2. What the model found. Something the user must do outranks a meeting
    #    mentioned in the same email: the meeting will be on the calendar
    #    anyway, while the task only exists if the user is told about it.
    if found & {"deadline_on_you", "question_pending"}:
        return Classification(
            ACTION_REQUIRED,
            "The analysis found something you need to do or answer.",
            SOURCE_LLM,
        )
    if "meeting" in found:
        return Classification(MEETING, "The analysis found a meeting in it.", SOURCE_LLM)

    # 3. Wording, trusted only in mail written by a person. A newsletter saying
    #    "please confirm" is asking for a click, not for the user's attention.
    human = not signals.mass_mailed and not signals.automated
    if human:
        if _INVITE_SUBJECT.search(signals.subject) or _MEETING_LINKS.search(signals.body_text):
            return Classification(MEETING, "A meeting invitation or meeting link.")
        request = _REQUEST.search(signals.text)
        if request:
            return Classification(
                ACTION_REQUIRED, f'Asks you to act: "{request.group(0).strip()}".'
            )
        if _MEETING_WORDS.search(signals.subject):
            return Classification(MEETING, "The subject is about a meeting.")

    # 4. The user's own priority list.
    if signals.vip_tier in {"CRITICAL", "IMPORTANT"}:
        return Classification(IMPORTANT, f"From a {signals.vip_tier} contact.")

    # 5. Machine-sent mail.
    if signals.mass_mailed and _PROMOTIONAL.search(signals.text):
        return Classification(LOW_PRIORITY, "A newsletter or promotion sent to a mailing list.")
    if signals.mass_mailed or signals.automated:
        return Classification(UPDATE, "An automated notification or mailing-list message.")

    if "deadline_from_others" in found:
        return Classification(
            UPDATE, "Someone committed to something for you; it is being tracked.", SOURCE_LLM
        )
    if signals.vip_tier == "MONITOR":
        return Classification(UPDATE, "From a MONITOR contact.")

    # 6. What remains is mail from an address nobody has ranked. A person's own
    #    mailbox, or a reply in a conversation the user is part of, is worth
    #    surfacing; an organisation writing in is far more often a notification.
    if signals.from_a_person:
        return Classification(IMPORTANT, "Written to you directly by a person.")
    if signals.is_reply:
        return Classification(IMPORTANT, "A reply in a conversation you are part of.")
    return Classification(UPDATE, "From an organisation that is not on your contacts list.")
