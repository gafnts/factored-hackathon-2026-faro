"""
Decision 18's retries, as built (ADR-0004's amendment of 2026-10-01; POL-48; OPS-04). A call that may be tried again
gets three attempts at most, each recorded on its own. A 429 or a 5xx waits the provider's retry-after when it is 10
seconds or less and ends the call when it is longer; any other failure waits a jittered backoff, up to 1 second before
the second attempt and up to 2 before the third; output that didn't parse is tried again at once. No attempt starts, and
no wait is taken that would cross it, once the turn has run 60 seconds. The sleep and the jitter are the policy's, so
tests and the evaluation's in-process player can run it without waiting.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

ATTEMPTS = 3
LONGEST_WAIT_S = 10.0
BACKOFF_S = 1.0
DEADLINE_S = 60.0


@dataclass(frozen=True)
class Wait:
    seconds: float
    waited_for: str

    def fields(self) -> dict[str, Any]:
        """
        What the attempt after the wait records.
        """
        return {"wait_ms": round(self.seconds * 1000), "waited_for": self.waited_for}


@dataclass(frozen=True)
class Retries:
    attempts: int = ATTEMPTS
    longest_wait_s: float = LONGEST_WAIT_S
    backoff_s: float = BACKOFF_S
    deadline_s: float = DEADLINE_S
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    jitter: Callable[[float, float], float] = random.uniform

    def after(
        self,
        attempt: int,
        elapsed_s: float,
        retry_after_s: float | None = None,
        at_once: bool = False,
    ) -> Wait | None:
        """
        The wait before the next attempt, given the attempt just made and how long the turn has run; None when the
        call ends here.
        """
        if attempt >= self.attempts:
            return None
        if retry_after_s is not None:
            if retry_after_s > self.longest_wait_s:
                return None
            wait = Wait(max(retry_after_s, 0.0), "retry_after")
        elif at_once:
            wait = Wait(0.0, "none")
        else:
            wait = Wait(
                self.jitter(0.0, self.backoff_s * 2 ** (attempt - 1)), "backoff"
            )
        if elapsed_s + wait.seconds >= self.deadline_s:
            return None
        return wait


RETRIES = Retries()


async def no_wait(_: float) -> None:
    """
    A sleep that returns at once, for tests and the evaluation's in-process player.
    """


def retry_after(headers: Mapping[str, str] | None) -> float | None:
    """
    The provider's retry-after, in seconds, from retry-after-ms or retry-after; None when neither is a number.
    """
    if not headers:
        return None
    for name, scale in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        value = headers.get(name)
        if value is None:
            continue
        try:
            return float(value) / scale
        except ValueError:
            continue
    return None
