"""How long to wait before retrying a failed job."""
from __future__ import annotations

import random
from typing import Callable

from src import config


def backoff_seconds(
    attempt: int,
    base: float | None = None,
    cap: float | None = None,
    rng: Callable[[], float] = random.random,
) -> float:
    """Delay before the retry that follows failed attempt number ``attempt``.

    Exponential, so a dependency that is down for a while is not hammered, and
    capped, so a job is never parked for hours. Then "equal jitter": the delay
    is somewhere between half and all of that ceiling. Without jitter, every
    email that failed while Ollama was down would retry in the same instant it
    came back, and hit it all at once.
    """
    base = config.JOB_BACKOFF_BASE_SECONDS if base is None else base
    cap = config.JOB_BACKOFF_CAP_SECONDS if cap is None else cap
    ceiling = min(cap, base * 2 ** max(attempt - 1, 0))
    return ceiling / 2 + rng() * ceiling / 2
