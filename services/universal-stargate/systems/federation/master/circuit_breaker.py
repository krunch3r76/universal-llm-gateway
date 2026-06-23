"""
Federation circuit breaker.

Prevents request storms to unhealthy federated gateways.

INVARIANT: ∀ request to unhealthy_gateway: circuit_open ⟹ fast_fail
STATE_MACHINE: CLOSED → OPEN → HALF_OPEN → CLOSED
"""

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from universal_event_bus import EventBus


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
    last_failure_status: int | None = None


@dataclass(slots=True)
class GatewayHealthStats:
    """Per-gateway transient-failure tracking — DEGRADED (timeouts) and UNHEALTHY
    (disconnects).

    Two parallel streak counters because timeouts and disconnects are
    structurally different failure modes:

    * Timeouts → DEGRADED: gateway is reachable but slow/saturated.
      Routing remains permissive; only a coordination signal fires so
      batch consumers can throttle. Cleared by ANY successful response.

    * Disconnects → UNHEALTHY: gateway is unreachable at the transport
      layer. Routing excludes via the existing `_gateway_wide_open` map
      and recovery_timeout; HALF_OPEN probes test recovery.

    Counting uses a streak-not-rate semantic: each counter resets to 0
    on any successful response. A gateway that intermittently succeeds
    will not accumulate across success boundaries. The design targets
    "is the gateway clearly broken / clearly degraded right now?" rather
    than "elevated failure rate". Operators who need rate-based health
    should query the event stream instead.
    """

    consecutive_timeouts: int = 0
    consecutive_disconnects: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    degraded_since: float | None = None  # None when not DEGRADED
    unhealthy_since: float | None = None  # None when not UNHEALTHY
    first_timeout_code: str | None = None
    first_disconnect_code: str | None = None


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
        gateway_failure_model_threshold: int = 3,
        gateway_timeout_threshold: int = 3,
        gateway_disconnect_threshold: int = 3,
        event_bus: "EventBus | None" = None,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout_seconds
        self._half_open_max = half_open_max_requests
        self._gateway_failure_model_threshold = gateway_failure_model_threshold
        # Consecutive REQUEST_TIMEOUT/INFERENCE_TIMEOUT/LOAD_TIMEOUT failures
        # before the gateway is marked DEGRADED (coordination signal only;
        # routing NOT excluded). Reset on any success.
        self._gateway_timeout_threshold = gateway_timeout_threshold
        # Consecutive GATEWAY_DISCONNECTED/EDGE_UNREACHABLE failures before
        # the gateway is marked UNHEALTHY (routing excluded for
        # recovery_timeout). Reset on any success.
        self._gateway_disconnect_threshold = gateway_disconnect_threshold
        self._event_bus = event_bus

        self._circuits: dict[tuple[str, str], CircuitStats] = {}
        self._half_open_requests: dict[tuple[str, str], int] = {}
        self._gateway_wide_open: dict[str, float] = {}
        self._gateway_health: dict[str, GatewayHealthStats] = {}

    async def _emit_rejected(
        self,
        gateway_id: str,
        model_id: str,
        reason: str,
    ) -> None:
        if self._event_bus is None:
            return
        from src.scheduling.events.federation_signaling import (
            FederationCircuitBreakerRequestRejected,
        )

        await self._event_bus.publish_nowait(
            FederationCircuitBreakerRequestRejected(
                gateway_id=gateway_id,
                model_id=model_id,
                reason=reason,
            )
        )

    def _pair_key(self, gateway_id: str, model_id: str) -> tuple[str, str]:
        return (gateway_id, model_id)

    def _is_gateway_wide_open(self, gateway_id: str) -> bool:
        opened_at = self._gateway_wide_open.get(gateway_id)
        if opened_at is None:
            return False
        time_in_open = time.time() - opened_at
        return time_in_open < self._recovery_timeout

    def is_gateway_unhealthy(self, gateway_id: str) -> bool:
        """Return True if this gateway is excluded from routing.

        Combines per-model threshold and disconnect-driven unhealthy state.
        Use this instead of accessing `_is_gateway_wide_open` directly.
        DEGRADED gateways (timeouts only) are NOT excluded — they remain
        routable. Use `is_gateway_degraded` to check that state.
        """
        return self._is_gateway_wide_open(gateway_id)

    def is_gateway_degraded(self, gateway_id: str) -> bool:
        """Return True if this gateway has been marked DEGRADED.

        Coordination state only — routing is NOT affected. Useful for
        scoring biases or batch-consumer throttle checks.
        """
        health = self._gateway_health.get(gateway_id)
        return health is not None and health.degraded_since is not None

    def _count_open_models(self, gateway_id: str) -> int:
        return sum(
            1
            for (gw_id, _), stats in self._circuits.items()
            if gw_id == gateway_id and self._evaluate_state(stats) == CircuitState.OPEN
        )

    def _update_gateway_wide_state(self, gateway_id: str) -> None:
        open_model_count = self._count_open_models(gateway_id)
        if open_model_count >= self._gateway_failure_model_threshold:
            if not self._is_gateway_wide_open(gateway_id):
                self._gateway_wide_open[gateway_id] = time.time()
                logger.warning(
                    "🔴 Gateway-wide circuit OPEN for %s after %d models failed",
                    gateway_id,
                    open_model_count,
                )
            return

        if gateway_id in self._gateway_wide_open:
            # Only clear if the gateway-wide-open was caused by per-model
            # threshold; if disconnect-driven UNHEALTHY is still asserted,
            # leave it to record_gateway_success / cooldown to clear.
            health = self._gateway_health.get(gateway_id)
            if health is None or health.unhealthy_since is None:
                self._gateway_wide_open.pop(gateway_id, None)
                logger.info(
                    "🟢 Gateway-wide circuit CLOSED for %s (open models: %d)",
                    gateway_id,
                    open_model_count,
                )

    def get_state(self, gateway_id: str, model_id: str) -> CircuitState:
        """Get current circuit state for a gateway/model pair."""
        stats = self._circuits.get(self._pair_key(gateway_id, model_id))
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

    async def should_allow_request(self, gateway_id: str, model_id: str) -> bool:
        """
        Check if request should be allowed to gateway.

        Returns:
            True if request should proceed, False if circuit is open
        """
        if self._is_gateway_wide_open(gateway_id):
            logger.warning(
                f"🔴 Gateway-wide circuit OPEN for {gateway_id} - rejecting request"
            )
            await self._emit_rejected(
                gateway_id=gateway_id,
                model_id=model_id,
                reason="gateway_wide_open",
            )
            return False

        pair_key = self._pair_key(gateway_id, model_id)
        stats = self._circuits.get(pair_key)
        if not stats:
            return True

        state = self._evaluate_state(stats)

        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            logger.warning(
                f"🔴 Circuit OPEN for {gateway_id}/{model_id} - rejecting request"
            )
            await self._emit_rejected(
                gateway_id=gateway_id,
                model_id=model_id,
                reason="model_circuit_open",
            )
            return False

        # HALF_OPEN - allow limited requests
        half_open_count = self._half_open_requests.get(pair_key, 0)
        if half_open_count >= self._half_open_max:
            logger.debug(
                f"🟡 Circuit HALF_OPEN limit reached for {gateway_id}/{model_id}"
            )
            await self._emit_rejected(
                gateway_id=gateway_id,
                model_id=model_id,
                reason="half_open_limit_reached",
            )
            return False

        self._half_open_requests[pair_key] = half_open_count + 1
        logger.info(
            f"🟡 Circuit HALF_OPEN for {gateway_id}/{model_id} "
            f"- allowing test request ({half_open_count + 1}/{self._half_open_max})"
        )
        return True

    async def record_success(self, gateway_id: str, model_id: str) -> None:
        """Record successful request to gateway/model pair."""
        pair_key = self._pair_key(gateway_id, model_id)
        stats = self._circuits.setdefault(pair_key, CircuitStats())
        stats.success_count += 1
        stats.last_success_time = time.time()

        state = self._evaluate_state(stats)

        if state == CircuitState.HALF_OPEN:
            stats.state = CircuitState.CLOSED
            stats.state_changed_at = time.time()
            stats.failure_count = 0
            self._half_open_requests.pop(pair_key, None)
            logger.info(
                f"🟢 Circuit CLOSED for {gateway_id}/{model_id} - recovery successful"
            )

        self._update_gateway_wide_state(gateway_id)

    async def record_failure(
        self,
        gateway_id: str,
        model_id: str,
        *,
        error: str | None = None,
        status_code: int | None = None,
    ) -> None:
        """Record failed request to gateway/model pair."""
        pair_key = self._pair_key(gateway_id, model_id)
        stats = self._circuits.setdefault(pair_key, CircuitStats())
        stats.failure_count += 1
        stats.last_failure_time = time.time()
        stats.last_failure_status = status_code

        state = self._evaluate_state(stats)

        if state == CircuitState.HALF_OPEN:
            stats.state = CircuitState.OPEN
            stats.state_changed_at = time.time()
            self._half_open_requests.pop(pair_key, None)
            logger.warning(
                f"🔴 Circuit reopened for {gateway_id}/{model_id} "
                f"- recovery failed: {error}"
            )
        elif state == CircuitState.CLOSED:
            if stats.failure_count >= self._failure_threshold:
                stats.state = CircuitState.OPEN
                stats.state_changed_at = time.time()
                logger.warning(
                    f"🔴 Circuit OPEN for {gateway_id}/{model_id} after "
                    f"{stats.failure_count} failures"
                )
        self._update_gateway_wide_state(gateway_id)

    async def record_gateway_timeout(
        self,
        gateway_id: str,
        *,
        error_code: str,
    ) -> None:
        """Record a timeout failure (REQUEST_TIMEOUT/INFERENCE_TIMEOUT/LOAD_TIMEOUT).

        Increments the consecutive-timeout counter. When the counter crosses
        `gateway_timeout_threshold` for the first time in the current streak,
        emits `federation.gateway.degraded`. Routing is NOT affected — the
        signal is for batch consumers to throttle voluntarily.
        """
        health = self._gateway_health.setdefault(gateway_id, GatewayHealthStats())
        health.consecutive_timeouts += 1
        health.last_failure_time = time.time()
        if health.first_timeout_code is None:
            health.first_timeout_code = error_code

        if (
            health.consecutive_timeouts >= self._gateway_timeout_threshold
            and health.degraded_since is None
        ):
            health.degraded_since = time.time()
            logger.warning(
                "🟠 Gateway %s marked DEGRADED after %d consecutive timeouts"
                " (first=%s)",
                gateway_id,
                health.consecutive_timeouts,
                health.first_timeout_code,
            )
            await self._emit_gateway_degraded(
                gateway_id=gateway_id,
                consecutive_timeouts=health.consecutive_timeouts,
                first_error_code=health.first_timeout_code or error_code,
            )

    async def record_gateway_disconnect(
        self,
        gateway_id: str,
        *,
        error_code: str,
    ) -> None:
        """Record a disconnect failure (GATEWAY_DISCONNECTED/EDGE_UNREACHABLE).

        Increments the consecutive-disconnect counter. When the counter
        crosses `gateway_disconnect_threshold` for the first time, emits
        `federation.gateway.unhealthy` AND adds the gateway to
        `_gateway_wide_open` so existing `should_allow_request` and
        `is_gateway_unhealthy` filtering excludes it for the cooldown
        window. The existing HALF_OPEN probe machinery handles recovery
        — when a probe succeeds, `record_gateway_success` clears the
        streak and emits `federation.gateway.recovered`.
        """
        health = self._gateway_health.setdefault(gateway_id, GatewayHealthStats())
        health.consecutive_disconnects += 1
        health.last_failure_time = time.time()
        if health.first_disconnect_code is None:
            health.first_disconnect_code = error_code

        if health.consecutive_disconnects >= self._gateway_disconnect_threshold:
            # Refresh the cooldown timestamp so each new disconnect inside
            # the window extends recovery — without this, a still-down gateway
            # becomes routable again after the original 30s elapses.
            self._gateway_wide_open[gateway_id] = time.time()
            if health.unhealthy_since is None:
                health.unhealthy_since = time.time()
                logger.warning(
                    "🔴 Gateway %s marked UNHEALTHY after %d consecutive"
                    " disconnects (first=%s)",
                    gateway_id,
                    health.consecutive_disconnects,
                    health.first_disconnect_code,
                )
                await self._emit_gateway_unhealthy(
                    gateway_id=gateway_id,
                    consecutive_disconnects=health.consecutive_disconnects,
                    first_error_code=health.first_disconnect_code or error_code,
                )

    async def record_gateway_success(self, gateway_id: str) -> None:
        """Clear timeout/disconnect streaks on any successful response.

        Called from the same place as `record_success` for any 2xx response.
        Emits `federation.gateway.recovered` for whichever state(s) were
        previously asserted (degradation, reachability, or both).
        """
        health = self._gateway_health.get(gateway_id)
        if health is None:
            return
        was_degraded = health.degraded_since is not None
        was_unhealthy = health.unhealthy_since is not None
        health.consecutive_timeouts = 0
        health.consecutive_disconnects = 0
        health.last_success_time = time.time()
        health.first_timeout_code = None
        health.first_disconnect_code = None
        health.degraded_since = None
        health.unhealthy_since = None
        if was_unhealthy:
            self._gateway_wide_open.pop(gateway_id, None)
            logger.info(
                "🟢 Gateway %s reachability RECOVERED after successful response",
                gateway_id,
            )
            await self._emit_gateway_recovered(
                gateway_id=gateway_id,
                kind="reachability",
                reason="probe_succeeded",
            )
        if was_degraded:
            logger.info(
                "🟢 Gateway %s degradation CLEARED after successful response",
                gateway_id,
            )
            await self._emit_gateway_recovered(
                gateway_id=gateway_id,
                kind="degradation",
                reason="first_success",
            )

    async def _emit_gateway_degraded(
        self,
        gateway_id: str,
        consecutive_timeouts: int,
        first_error_code: str,
    ) -> None:
        if self._event_bus is None:
            return
        from src.scheduling.events.federation_signaling import (
            FederationGatewayDegraded,
        )

        await self._event_bus.publish_nowait(
            FederationGatewayDegraded(
                gateway_id=gateway_id,
                consecutive_timeouts=consecutive_timeouts,
                first_error_code=first_error_code,
            )
        )

    async def _emit_gateway_unhealthy(
        self,
        gateway_id: str,
        consecutive_disconnects: int,
        first_error_code: str,
    ) -> None:
        if self._event_bus is None:
            return
        from src.scheduling.events.federation_signaling import (
            FederationGatewayUnhealthy,
        )

        await self._event_bus.publish_nowait(
            FederationGatewayUnhealthy(
                gateway_id=gateway_id,
                consecutive_disconnects=consecutive_disconnects,
                first_error_code=first_error_code,
                cooldown_s=self._recovery_timeout,
            )
        )

    async def _emit_gateway_recovered(
        self,
        gateway_id: str,
        kind: str,
        reason: str,
        downtime_ms: int | None = None,
    ) -> None:
        if self._event_bus is None:
            return
        from src.scheduling.events.federation_signaling import (
            FederationGatewayRecovered,
        )

        await self._event_bus.publish_nowait(
            FederationGatewayRecovered(
                gateway_id=gateway_id,
                kind=kind,
                reason=reason,
                downtime_ms=downtime_ms,
            )
        )

    async def emit_gateway_liveness_stale(
        self,
        gateway_id: str,
        heartbeat_age_ms: int,
        threshold_ms: int,
        last_heartbeat_iso: str,
        backend_type: str,
    ) -> None:
        """Publish passive liveness staleness alert (no routing mutation)."""
        if self._event_bus is None:
            return
        from src.scheduling.events.federation_signaling import (
            FederationGatewayLivenessStale,
        )

        await self._event_bus.publish_nowait(
            FederationGatewayLivenessStale(
                gateway_id=gateway_id,
                heartbeat_age_ms=heartbeat_age_ms,
                threshold_ms=threshold_ms,
                last_heartbeat_iso=last_heartbeat_iso,
                backend_type=backend_type,
            )
        )

    async def emit_gateway_liveness_recovered(
        self,
        gateway_id: str,
        downtime_ms: int,
    ) -> None:
        """Publish liveness recovery (heartbeat resumed under 60s)."""
        await self._emit_gateway_recovered(
            gateway_id=gateway_id,
            kind="liveness",
            reason="heartbeat_resumed",
            downtime_ms=downtime_ms,
        )

    def get_all_states(self) -> dict[str, Any]:
        """Get all circuit states for health reporting."""
        per_model: dict[str, dict[str, Any]] = {}
        for (gateway_id, model_id), stats in self._circuits.items():
            gateway_states = per_model.setdefault(gateway_id, {})
            gateway_states[model_id] = {
                "state": self._evaluate_state(stats).value,
                "failure_count": stats.failure_count,
                "success_count": stats.success_count,
                "last_failure": stats.last_failure_time,
                "last_success": stats.last_success_time,
                "last_failure_status": stats.last_failure_status,
            }

        return {
            "per_model": per_model,
            "gateway_wide": {
                gateway_id: {
                    "state": (
                        CircuitState.OPEN.value
                        if self._is_gateway_wide_open(gateway_id)
                        else CircuitState.CLOSED.value
                    ),
                    "opened_at": opened_at,
                }
                for gateway_id, opened_at in self._gateway_wide_open.items()
            },
        }

    async def reset(self, gateway_id: str, model_id: str | None = None) -> None:
        """Reset circuit for gateway/model pair or entire gateway."""
        if model_id is None:
            keys_to_reset = [
                pair_key for pair_key in self._circuits if pair_key[0] == gateway_id
            ]
            for pair_key in keys_to_reset:
                self._circuits[pair_key] = CircuitStats()
                self._half_open_requests.pop(pair_key, None)
            self._gateway_wide_open.pop(gateway_id, None)
            logger.info(f"🔄 Circuit reset for gateway {gateway_id}")
            return

        pair_key = self._pair_key(gateway_id, model_id)
        if pair_key in self._circuits:
            self._circuits[pair_key] = CircuitStats()
            self._half_open_requests.pop(pair_key, None)
            self._update_gateway_wide_state(gateway_id)
            logger.info(f"🔄 Circuit reset for {gateway_id}/{model_id}")

    def is_request_allowed_sync(self, gateway_id: str, model_id: str) -> bool:
        """
        Check if request is allowed (synchronous, read-only).

        Used by router for filtering candidates.
        """
        if self._is_gateway_wide_open(gateway_id):
            return False

        pair_key = self._pair_key(gateway_id, model_id)
        stats = self._circuits.get(pair_key)
        if not stats:
            return True

        state = self._evaluate_state(stats)

        if state == CircuitState.OPEN:
            return False

        if state == CircuitState.HALF_OPEN:
            half_open_count = self._half_open_requests.get(pair_key, 0)
            if half_open_count >= self._half_open_max:
                return False

        return True
