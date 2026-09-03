"""Retrying a call that might just be having a bad moment.

This is the inner of two defences. Backoff handles the blip — a dropped
connection, a gateway hiccup, one request that timed out — inside a single sync
run. The circuit breaker in `breaker` handles the outage, across runs. They are
separate because the right response is different: retrying is correct when the
next attempt might work, and wrong when it certainly will not.

Which is why `retry_on` has no default. A 500 or a timeout deserves another go;
a 401 does not, and will not until someone rotates a credential. Retrying it
turns one authentication failure into three, delays the honest error by the
whole backoff budget, and can lock the account. The caller names the exceptions
worth repeating, and everything else propagates on the first raise.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from lnd.config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    """How many goes, how long between them.

    Delays grow geometrically and are capped, so a slow source costs a bounded
    amount of the sync window rather than an unbounded one.
    """

    attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    multiplier: float = 2.0
    jitter: float = 0.25

    @classmethod
    def from_settings(cls) -> RetryPolicy:
        settings = get_settings()
        return cls(
            attempts=settings.retry_attempts,
            base_delay=settings.retry_base_delay_seconds,
            max_delay=settings.retry_max_delay_seconds,
            jitter=settings.retry_jitter,
        )

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait after `attempt` (1-based) has failed.

        Jitter is applied after the cap and is symmetric, so the mean delay is
        the nominal one. It exists because five entities that failed in the same
        beat tick would otherwise retry at exactly the same instant, three times
        over, and arrive at a recovering source as one spike each time.
        """
        if attempt < 1:
            raise ValueError("attempt is 1-based")

        delay = min(self.base_delay * (self.multiplier ** (attempt - 1)), self.max_delay)
        if self.jitter:
            # Not a security decision — spreading a herd, so the standard
            # generator is the right tool.
            delay *= 1 + random.uniform(-self.jitter, self.jitter)  # noqa: S311
        return max(delay, 0.0)


def retry[T](
    operation: Callable[[], T],
    *,
    retry_on: tuple[type[Exception], ...],
    policy: RetryPolicy | None = None,
    describe: str = "operation",
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `operation`, repeating it while it raises one of `retry_on`.

    Returns its result, or re-raises the last failure once the attempts are
    spent — so the caller sees the real exception rather than a wrapper, and
    `record_sync_run` stamps the source's own error type on the run.

    `sleep` is injectable so tests exercise the delays without waiting them.
    """
    policy = policy or RetryPolicy.from_settings()

    for attempt in range(1, policy.attempts + 1):
        try:
            return operation()
        except retry_on as exc:
            if attempt == policy.attempts:
                log.warning(
                    "retries exhausted",
                    extra={
                        "event": "sync.retry.exhausted",
                        "operation": describe,
                        "attempts": attempt,
                        "error_type": type(exc).__name__,
                    },
                )
                raise

            delay = policy.delay_for(attempt)
            log.info(
                "retrying after failure",
                extra={
                    "event": "sync.retry",
                    "operation": describe,
                    "attempt": attempt,
                    "delay_seconds": round(delay, 2),
                    "error_type": type(exc).__name__,
                },
            )
            sleep(delay)

    # `policy.attempts` is at least 1, so the loop either returns or raises.
    raise AssertionError("unreachable")
