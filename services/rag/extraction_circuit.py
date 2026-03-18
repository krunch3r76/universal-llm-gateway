"""Shared circuit breaker for RAG extraction → Stargate calls.

All watcher workers check should_attempt() before calling extraction.
Consecutive capacity failures open the circuit; a cooldown period must
elapse before a single probe attempt (half-open).  Probe success closes
the circuit; probe failure reopens with doubled cooldown.

Thread-safe via asyncio.Lock (single event loop, multiple coroutines).
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class ExtractionCircuit:
    """Shared circuit breaker for extraction → Stargate capacity protection.

    Three states: CLOSED (normal), OPEN (extraction paused), HALF_OPEN (probing).
    Only capacity errors open the circuit — parse failures and model bugs don't
    indicate infrastructure saturation.

    Lock-free fast path: should_attempt() returns True immediately when CLOSED
    (the common case).  The lock is only acquired on OPEN/HALF_OPEN transitions.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        base_cooldown_s: float = 30.0,
        max_cooldown_s: float = 300.0,
    ) -> None:
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._failure_threshold = failure_threshold
        self._base_cooldown_s = base_cooldown_s
        self._max_cooldown_s = max_cooldown_s
        self._current_cooldown_s = base_cooldown_s
        self._open_since: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def current_cooldown_s(self) -> float:
        return self._current_cooldown_s

    async def should_attempt(self) -> bool:
        """Check if extraction should proceed.  Non-blocking for CLOSED state."""
        if self._state == CircuitState.CLOSED:
            return True
        async with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - self._open_since
                if elapsed >= self._current_cooldown_s:
                    self._state = CircuitState.HALF_OPEN
                    return True
                return False
            if self._state == CircuitState.HALF_OPEN:
                return False  # Probe already in flight
            return True  # CLOSED (race with lock acquisition)

    async def record_success(self) -> bool:
        """Extraction succeeded — close circuit, reset counters.

        Returns True if the circuit transitioned from OPEN/HALF_OPEN to CLOSED.
        """
        async with self._lock:
            was_open = self._state != CircuitState.CLOSED
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._current_cooldown_s = self._base_cooldown_s
            if was_open:
                logger.info("Extraction circuit breaker closed — capacity recovered")
            return was_open

    async def record_failure(self, *, capacity_error: bool = False) -> bool:
        """Record extraction failure.  Only capacity errors count.

        Returns True if the circuit just transitioned to OPEN.
        """
        if not capacity_error:
            return False
        async with self._lock:
            self._consecutive_failures += 1
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._open_since = time.monotonic()
                self._current_cooldown_s = min(
                    self._current_cooldown_s * 2, self._max_cooldown_s
                )
                logger.warning(
                    "Circuit breaker probe failed — reopening (cooldown %.0fs)",
                    self._current_cooldown_s,
                )
                return True
            elif self._consecutive_failures >= self._failure_threshold:
                self._state = CircuitState.OPEN
                self._open_since = time.monotonic()
                logger.warning(
                    "Circuit breaker opened after %d consecutive capacity failures"
                    " (cooldown %.0fs)",
                    self._consecutive_failures,
                    self._current_cooldown_s,
                )
                return True
            return False
