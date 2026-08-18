"""In-process circuit breaker in front of Gemini. One breaker per process,
shared by every task -- an outage of the provider isn't task-specific, so a
single breaker is what the failure actually looks like.

ponytail: per-process state, not Redis-backed. At 2 replicas and tens of
calls/hour, two pods learning about an outage independently costs one extra
failed call each -- not worth a Redis dependency this repo doesn't otherwise
have. Move to Redis when replica count or call volume makes that duplication
cost real (docs/services.md §4, docs/ai-module-plan.md §0.5).
"""

import threading
import time
from dataclasses import dataclass
from typing import Literal

State = Literal["CLOSED", "OPEN", "HALF_OPEN"]


@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: int = 5      # consecutive 5xx/timeouts before OPEN
    recovery_seconds: float = 30.0  # OPEN -> HALF_OPEN after this long
    half_open_probes: int = 1       # consecutive successes in HALF_OPEN before CLOSED


class CircuitBreaker:
    """Trips on 5xx and timeouts only -- never on 429. A 429 is the provider
    asking this one call to back off; it is not evidence the provider is
    down, and tripping the whole service off Gemini because one caller was
    noisy would be the wrong reaction (see core.py's `_Retryable` split,
    which already keeps 429 out of this path)."""

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self._config = config or CircuitBreakerConfig()
        self._state: State = "CLOSED"
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> State:
        with self._lock:
            self._maybe_recover()
            return self._state

    def _maybe_recover(self) -> None:
        # Caller already holds _lock.
        if self._state == "OPEN" and (
            time.monotonic() - self._opened_at >= self._config.recovery_seconds
        ):
            self._state = "HALF_OPEN"
            self._consecutive_successes = 0

    def allow(self) -> bool:
        """True if a call may proceed. False means: skip the network call and
        degrade immediately -- see ai/core.py's `ask_ai`."""
        with self._lock:
            self._maybe_recover()
            return self._state != "OPEN"

    def record(self, ok: bool) -> None:
        """Report the outcome of a call that `allow()` let through."""
        with self._lock:
            if self._state == "HALF_OPEN":
                if not ok:
                    self._state = "OPEN"
                    self._opened_at = time.monotonic()
                    self._consecutive_successes = 0
                    return
                self._consecutive_successes += 1
                if self._consecutive_successes >= self._config.half_open_probes:
                    self._state = "CLOSED"
                    self._consecutive_failures = 0
                    self._consecutive_successes = 0
                return

            if ok:
                self._consecutive_failures = 0
                return

            self._consecutive_failures += 1
            if self._consecutive_failures >= self._config.failure_threshold:
                self._state = "OPEN"
                self._opened_at = time.monotonic()
                self._consecutive_failures = 0
