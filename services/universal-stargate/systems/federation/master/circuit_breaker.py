"""
Federation circuit breaker.

Prevents request storms to unhealthy federated gateways.

INVARIANT: ∀ request to unhealthy_gateway: circuit_open ⟹ fast_fail
STATE_MACHINE: CLOSED → OPEN → HALF_OPEN → CLOSED
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation, requests pass through
    OPEN = "open"  # Failing, requests rejected
    HALF_OPEN = "half_open"  # Testing, limited requests allowed


@dataclass(slots=True)
class CircuitStats:
    """Per-gateway circuit statistics."""

    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    state: CircuitState = CircuitState.CLOSED
    state_changed_at: float = field(default_factory=time.time)


class FederationCircuitBreaker:
    """
    Circuit breaker for federated gateway health.

    Prevents request storms to unhealthy gateways by:
    1. Tracking failure/success per gateway
    2. Opening circuit after threshold failures
    3. Testing recovery with half-open state
    4. Closing circuit after successful recovery

    INVARIANT:
        circuit_state(gw) = OPEN ⟹ ¬route_to(gw)
        failure_count(gw) ≥ threshold ⟹ circuit_state(gw) = OPEN
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        half_open_max_requests: int = 3,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout_seconds
        self._half_open_max = half_open_max_requests

        self._circuits: dict[str, CircuitStats] = {}
        self._half_open_requests: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def get_state(self, gateway_id: str) -> CircuitState:
        """Get current circuit state for gateway."""
        stats = self._circuits.get(gateway_id)
        if not stats:
            return CircuitState.CLOSED
        return self._evaluate_state(stats)

    def _evaluate_state(self, stats: CircuitStats) -> CircuitState:
        """Evaluate current state based on stats and timeouts."""
        if stats.state == CircuitState.OPEN:
            time_in_open = time.time() - stats.state_changed_at
            if time_in_open >= self._recovery_timeout:
                return CircuitState.HALF_OPEN
        return stats.state

    async def should_allow_request(self, gateway_id: str) -> bool:
        """
        Check if request should be allowed to gateway.

        Returns:
            True if request should proceed, False if circuit is open
        """
        async with self._lock:
            stats = self._circuits.get(gateway_id)
            if not stats:
                return True

            state = self._evaluate_state(stats)

            if state == CircuitState.CLOSED:
                return True

            if state == CircuitState.OPEN:
                logger.warning(f"🔴 Circuit OPEN for {gateway_id} - rejecting request")
                return False

            # HALF_OPEN - allow limited requests
            half_open_count = self._half_open_requests.get(gateway_id, 0)
            if half_open_count >= self._half_open_max:
                logger.debug(f"🟡 Circuit HALF_OPEN limit reached for {gateway_id}")
                return False

            self._half_open_requests[gateway_id] = half_open_count + 1
            logger.info(
                f"🟡 Circuit HALF_OPEN for {gateway_id} - allowing test request "
                f"({half_open_count + 1}/{self._half_open_max})"
            )
            return True

    async def record_success(self, gateway_id: str) -> None:
        """Record successful request to gateway."""
        async with self._lock:
            stats = self._circuits.setdefault(gateway_id, CircuitStats())
            stats.success_count += 1
            stats.last_success_time = time.time()

            state = self._evaluate_state(stats)

            if state == CircuitState.HALF_OPEN:
                stats.state = CircuitState.CLOSED
                stats.state_changed_at = time.time()
                stats.failure_count = 0
                self._half_open_requests.pop(gateway_id, None)
                logger.info(f"🟢 Circuit CLOSED for {gateway_id} - recovery successful")

    async def record_failure(self, gateway_id: str, error: str | None = None) -> None:
        """Record failed request to gateway."""
        async with self._lock:
            stats = self._circuits.setdefault(gateway_id, CircuitStats())
            stats.failure_count += 1
            stats.last_failure_time = time.time()

            state = self._evaluate_state(stats)

            if state == CircuitState.HALF_OPEN:
                stats.state = CircuitState.OPEN
                stats.state_changed_at = time.time()
                self._half_open_requests.pop(gateway_id, None)
                logger.warning(
                    f"🔴 Circuit reopened for {gateway_id} - recovery failed: {error}"
                )
            elif state == CircuitState.CLOSED:
                if stats.failure_count >= self._failure_threshold:
                    stats.state = CircuitState.OPEN
                    stats.state_changed_at = time.time()
                    logger.warning(
                        f"🔴 Circuit OPEN for {gateway_id} after "
                        f"{stats.failure_count} failures"
                    )

    def get_all_states(self) -> dict[str, dict[str, Any]]:
        """Get all circuit states for health reporting."""
        return {
            gw_id: {
                "state": self._evaluate_state(stats).value,
                "failure_count": stats.failure_count,
                "success_count": stats.success_count,
                "last_failure": stats.last_failure_time,
                "last_success": stats.last_success_time,
            }
            for gw_id, stats in self._circuits.items()
        }

    async def reset(self, gateway_id: str) -> None:
        """Reset circuit for gateway (manual intervention)."""
        async with self._lock:
            if gateway_id in self._circuits:
                self._circuits[gateway_id] = CircuitStats()
                self._half_open_requests.pop(gateway_id, None)
                logger.info(f"🔄 Circuit reset for {gateway_id}")

    def is_request_allowed_sync(self, gateway_id: str) -> bool:
        """
        Check if request is allowed (synchronous, read-only).

        Used by router for filtering candidates.
        Note: Accesses shared state without lock (acceptable for routing hint).
        """
        stats = self._circuits.get(gateway_id)
        if not stats:
            return True

        state = self._evaluate_state(stats)

        if state == CircuitState.OPEN:
            return False

        if state == CircuitState.HALF_OPEN:
            # Best-effort check without lock
            half_open_count = self._half_open_requests.get(gateway_id, 0)
            if half_open_count >= self._half_open_max:
                return False

        return True
