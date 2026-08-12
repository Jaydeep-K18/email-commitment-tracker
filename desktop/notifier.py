"""Desktop notifications when a background cycle finds new commitments.

The scheduler reports *how many* commitments a cycle extracted but not which
ones, so "new" is defined here as "id above the highest one we had already
seen". The high-water mark is primed once at startup, which is what stops a
first run announcing the entire existing backlog as though it had just arrived.

An id comparison is preferred over ``Commitment.created_at`` deliberately: ids
are monotonic and immutable, so the check cannot be confused by a clock that
moves backwards (DST, an NTP correction, a laptop waking in another timezone).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Sequence

from sqlalchemy import func, select

from dashboard import data
from src.storage.database import session_scope
from src.storage.models import Commitment

log = logging.getLogger(__name__)

#: Statuses that mean the commitment is not something to announce.
_QUIET_STATUSES = frozenset({"dismissed", "superseded"})

#: How many subjects to name before falling back to a bare count.
_MAX_NAMED = 2


@dataclass(frozen=True)
class Notice:
    """One notification, ready to hand to the tray."""

    title: str
    message: str


def highest_commitment_id(session) -> int:
    """The largest commitment id currently stored, or 0 when there are none."""
    return session.execute(select(func.max(Commitment.id))).scalar() or 0


def commitments_above(session, high_water: int) -> list[Commitment]:
    """Commitments stored since ``high_water``, most urgent first."""
    rows = session.execute(
        select(Commitment).where(Commitment.id > high_water)
    ).scalars().all()
    live = [row for row in rows if row.status not in _QUIET_STATUSES]
    # Undated commitments sort last; among dated ones the soonest leads.
    return sorted(live, key=lambda c: (c.deadline is None, c.deadline or 0))


def build_notice(commitments: Sequence[Commitment]) -> Notice | None:
    """Describe a batch of new commitments, or None if there are none.

    The subject line of the most urgent one is worth more than a count on its
    own — a notification saying "3 new commitments" gives the user no reason to
    open the app, where naming the soonest deadline does.
    """
    if not commitments:
        return None

    count = len(commitments)
    title = "1 new commitment" if count == 1 else f"{count} new commitments"

    named = commitments[:_MAX_NAMED]
    lines = []
    for commitment in named:
        when = data.format_deadline(commitment.deadline)
        lines.append(f"• {commitment.subject} — {when}")
    remaining = count - len(named)
    if remaining > 0:
        lines.append(f"…and {remaining} more")
    return Notice(title=title, message="\n".join(lines))


class CommitmentNotifier:
    """Watches for newly stored commitments and announces them once.

    ``send`` is injected rather than imported so this is testable without a tray
    icon, and so a platform whose backend cannot show notifications can pass a
    no-op instead of crashing the supervisor.
    """

    def __init__(self, send: Callable[[str, str], None]) -> None:
        self._send = send
        self._high_water = 0
        self._primed = False

    @property
    def high_water(self) -> int:
        return self._high_water

    def prime(self) -> None:
        """Record what already exists, so only later arrivals are announced."""
        try:
            with session_scope() as session:
                self._high_water = highest_commitment_id(session)
        except Exception:  # noqa: BLE001 - a missing database must not stop startup
            log.debug("Could not prime the notifier high-water mark.", exc_info=True)
            self._high_water = 0
        self._primed = True

    def poll(self) -> Notice | None:
        """Announce anything stored since the last call. Returns what was sent."""
        if not self._primed:
            self.prime()
            return None
        try:
            with session_scope() as session:
                fresh = commitments_above(session, self._high_water)
                notice = build_notice(fresh)
                # Advance past everything seen, including the quiet statuses
                # filtered out above, so they are not reconsidered next cycle.
                self._high_water = max(
                    self._high_water, highest_commitment_id(session)
                )
        except Exception:  # noqa: BLE001 - notifications are never load-bearing
            log.debug("Could not check for new commitments.", exc_info=True)
            return None

        if notice is not None:
            try:
                self._send(notice.title, notice.message)
            except Exception:  # noqa: BLE001 - a failed toast must not kill the cycle
                log.debug("Could not show a desktop notification.", exc_info=True)
        return notice
