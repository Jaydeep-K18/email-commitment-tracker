"""The API the Gmail side panel talks to (Phase 10).

The extension runs inside Gmail and can see the message the user is reading. It
sends that text here, this process runs the *same* local extraction the
background pipeline uses, and hands back what it found. Nothing is stored until
the user presses a button.

Everything here is guarded by :mod:`src.server.api_token` — see that module for
why loopback alone is not a security boundary. The extraction itself still runs
against a local model, so opening the panel does not send the user's email
anywhere.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from src import config
from src.classification.service import apply_classification
from src.collection.email_parser import ParsedEmail
from src.events.recorder import COMMITMENT_CREATED, email_correlation, record_event
from src.filtering import vip_filter
from src.server import api_token
from src.storage import database
from src.storage.database import session_scope
from src.storage.models import RawEmail
from src.sync import conflict_resolver

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["extension"])


# --- Auth ------------------------------------------------------------------

def require_token(
    x_tracker_token: str | None = Header(default=None, alias=api_token.TOKEN_HEADER),
) -> None:
    """Reject anything that does not present the local token.

    Applied to every route in this router. A 401 rather than a 403: the caller
    has not identified itself at all.
    """
    if not api_token.token_matches(x_tracker_token):
        raise HTTPException(
            status_code=401,
            detail=(
                "Missing or invalid tracker token. Open the tracker's setup "
                "page and paste the token into the extension's options."
            ),
        )


# --- Wire format -----------------------------------------------------------

class MessagePayload(BaseModel):
    """One email as the extension scraped it out of the Gmail page."""

    subject: str = ""
    sender_name: str = ""
    sender_email: str = ""
    body: str = ""
    #: Gmail's own id for the thread, used only to spot repeat lookups.
    thread_id: str | None = None

    def is_readable(self) -> bool:
        """Whether there is enough here to be worth asking the model about.

        Gmail's DOM changes without notice, so the panel can end up with an
        empty body. Saying so is much better than sending nothing to the model
        and reporting "no commitments found", which looks identical to a
        genuinely uneventful email.
        """
        return bool(self.body.strip()) and len(self.body.strip()) >= 20


class CommitmentPayload(BaseModel):
    """A commitment the user has chosen to keep."""

    type: str = "deadline_on_you"
    subject: str = Field(min_length=1)
    deadline: datetime | None = None
    counterparty: str | None = None
    evidence_quote: str = ""
    confidence: float = 0.8
    message: MessagePayload


class FoundCommitment(BaseModel):
    """What the panel renders for each thing the model found."""

    type: str
    subject: str
    deadline: datetime | None
    counterparty: str | None
    evidence_quote: str
    confidence: float


class AnalyzeResponse(BaseModel):
    ok: bool
    readable: bool = True
    commitments: list[FoundCommitment] = Field(default_factory=list)
    #: Populated when the model ran but everything it produced was discarded,
    #: so the panel can explain *why* it is showing nothing.
    discarded: int = 0
    message: str = ""


# --- Helpers ---------------------------------------------------------------

def _as_raw_email(payload: MessagePayload) -> RawEmail:
    """An unsaved RawEmail, so the extraction pipeline can be reused as-is.

    Building the same object the background path uses is what keeps the panel
    and the scheduler from ever disagreeing about the contents of an email.
    """
    return RawEmail(
        message_id=f"panel:{payload.thread_id or 'unknown'}",
        thread_id=payload.thread_id,
        sender_name=payload.sender_name or None,
        sender_email=payload.sender_email or None,
        subject=payload.subject or None,
        body_text=payload.body,
        received_at=datetime.now(),
    )


def _find_existing(session, payload: MessagePayload):
    """An already-stored commitment that looks like this message, if any.

    Reuses the conflict resolver's subject similarity rather than inventing a
    second notion of "the same thing", so the panel agrees with the deduping the
    sync engine already does.
    """
    best = None
    best_score = 0.0
    for commitment in database.open_commitments(session):
        score = conflict_resolver.subject_similarity(
            commitment.subject, payload.subject
        )
        if score > best_score:
            best, best_score = commitment, score
    if best is not None and best_score >= config.SYNC_DUPLICATE_THRESHOLD:
        return best, best_score
    return None, 0.0


# --- Routes ----------------------------------------------------------------

@router.get("/status", dependencies=[Depends(require_token)])
def status() -> dict:
    """Health check for the panel, and what the app can currently do."""
    from src.auth import google_auth

    with session_scope() as session:
        open_count = len(database.open_commitments(session))

    return {
        "ok": True,
        "google_connected": google_auth.is_signed_in(),
        "calendar_url": (
            f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/calendar.ics"
        ),
        "open_commitments": open_count,
        "model": config.OLLAMA_MODEL,
    }


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    dependencies=[Depends(require_token)],
)
def analyze(payload: MessagePayload) -> AnalyzeResponse:
    """Run local extraction over the open message without storing anything."""
    if not payload.is_readable():
        return AnalyzeResponse(
            ok=True,
            readable=False,
            message=(
                "Could not read the message text from this page. Open the email "
                "fully (not the preview pane) and try again."
            ),
        )

    from src.extraction.ollama_client import OllamaClient
    from src.extraction.pipeline import extract_from_email

    email = _as_raw_email(payload)
    try:
        outcome = extract_from_email(OllamaClient(), email)
    except Exception as exc:  # noqa: BLE001 - reported to the panel as text
        log.warning("Panel extraction failed: %s", exc)
        return AnalyzeResponse(
            ok=False,
            message=(
                f"The local model could not be reached ({exc}). Is Ollama "
                "running?"
            ),
        )

    found = [
        FoundCommitment(
            type=c.type.value,
            subject=c.subject,
            deadline=c.deadline,
            counterparty=c.counterparty,
            evidence_quote=c.evidence_quote,
            confidence=c.confidence,
        )
        for c in outcome.commitments
    ]

    message = ""
    if not found and outcome.discarded:
        # Distinguish "the model found nothing" from "the model produced things
        # that failed the evidence check", which are very different signals.
        message = (
            f"Found {outcome.discarded} possible item(s) but discarded them: "
            "the supporting quote was not actually in the email."
        )
    elif not found:
        message = "No deadlines or commitments in this email."

    return AnalyzeResponse(
        ok=outcome.ok,
        commitments=found,
        discarded=outcome.discarded,
        message=message,
    )


@router.get("/lookup", dependencies=[Depends(require_token)])
def lookup(subject: str = "", thread_id: str = "") -> dict:
    """Whether this message already produced a commitment the app is tracking."""
    payload = MessagePayload(subject=subject, thread_id=thread_id or None)
    with session_scope() as session:
        existing, score = _find_existing(session, payload)
        if existing is None:
            return {"known": False}
        return {
            "known": True,
            "id": existing.id,
            "subject": existing.subject,
            "deadline": existing.deadline.isoformat() if existing.deadline else None,
            "on_calendar": bool(existing.calendar_synced),
            "status": existing.status,
            "similarity": round(score, 2),
        }


@router.post("/commitments", dependencies=[Depends(require_token)])
def create_commitment(payload: CommitmentPayload) -> dict:
    """Store a commitment the user approved from the panel, and calendar it.

    Approval is explicit here — the user pressed a button — so ``sync_approved``
    is set rather than leaving it to wait in the review queue. It still goes
    through the same tier policy as everything else at publish time.
    """
    from src.extraction.schemas import ExtractedCommitment

    try:
        extracted = ExtractedCommitment(
            type=payload.type,
            subject=payload.subject,
            deadline=payload.deadline,
            counterparty=payload.counterparty,
            evidence_quote=payload.evidence_quote or payload.subject,
            confidence=payload.confidence,
        )
    except Exception as exc:  # noqa: BLE001 - a 422 with the reason is enough
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    message = payload.message
    with session_scope() as session:
        rules = vip_filter.load_rules(session)
        decision = vip_filter.assign_tier(
            rules, message.sender_email or "", message.sender_name or ""
        )

        parsed = ParsedEmail(
            message_id=f"panel:{message.thread_id or payload.subject}",
            thread_id=message.thread_id,
            sender_name=message.sender_name or None,
            sender_email=message.sender_email or None,
            recipient_email=config.IMAP_USER or None,
            subject=message.subject or payload.subject,
            body_text=message.body,
            received_at=datetime.now(),
        )
        # Already processed: the panel has just done the extraction, and the
        # background pipeline must not pick this up and run the model again.
        email = database.save_email(
            session, parsed, vip_tier=decision.tier, processed=True
        )
        if email is None:
            # Same Message-ID as an earlier panel add — reuse that email row so
            # pressing the button twice cannot orphan the second commitment.
            email = database.email_by_message_id(session, parsed.message_id)
        if email is None:
            raise HTTPException(
                status_code=500, detail="Could not record the source email."
            )

        commitment = database.save_commitment(session, email, extracted)
        # Both flags, and they mean different things: approved so it publishes,
        # and manually added so a SKIP-tier sender cannot veto a choice the user
        # made while looking at the email.
        commitment.manually_added = True
        database.set_sync_approval(session, commitment.id, True)

        record_event(
            session,
            COMMITMENT_CREATED,
            f"Added from the Gmail panel: {commitment.subject}",
            entity_type="commitment",
            entity_id=commitment.id,
            correlation_id=email_correlation(email.id),
            severity="success",
            payload={"type": commitment.type, "source": "gmail_panel"},
        )
        apply_classification(session, email)

        return {
            "ok": True,
            "id": commitment.id,
            "subject": commitment.subject,
            "tier": decision.tier,
            "deadline": (
                commitment.deadline.isoformat() if commitment.deadline else None
            ),
        }


@router.post("/sync", dependencies=[Depends(require_token)])
def sync_now() -> dict:
    """Publish immediately, so a panel add reaches the calendar without waiting.

    Without this the user would press "Add to calendar" and then watch nothing
    happen for up to thirty minutes until the next scheduled cycle.
    """
    from src.sync.sync_engine import run_sync

    with session_scope() as session:
        report = run_sync(session)

    return {
        "ok": report.ok,
        "published": report.published,
        "google_connected": report.google_connected,
        "google_created": report.google_created,
        "errors": report.errors + report.google_errors,
    }
