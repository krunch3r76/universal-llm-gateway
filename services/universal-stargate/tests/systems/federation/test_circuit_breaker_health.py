"""Unit tests for FederationCircuitBreaker gateway health methods.

Covers the Phase 2 additions:
- record_gateway_timeout → DEGRADED (coordination only, ¬routing exclusion)
- record_gateway_disconnect → UNHEALTHY (routing excluded for cooldown)
- record_gateway_success → clears whichever state(s) were asserted

The breaker has no event bus by default; tests inject a `FakeEventBus` that
records published events so we can assert on signals + payloads without
touching the real event-service transport.
"""

from __future__ import annotations

import pytest

from src.scheduling.events.federation_signaling import (
    FEDERATION_GATEWAY_DEGRADED,
    FEDERATION_GATEWAY_RECOVERED,
    FEDERATION_GATEWAY_UNHEALTHY,
)
from systems.federation.master.circuit_breaker import (
    FederationCircuitBreaker,
)


class FakeEventBus:
    """Minimal EventBus stand-in: records every event passed to publish_nowait."""

    def __init__(self) -> None:
        self.events: list = []

    async def publish_nowait(self, event) -> None:
        self.events.append(event)

    def signals(self) -> list[str]:
        return [e.signal for e in self.events]

    def by_signal(self, signal: str) -> list:
        return [e for e in self.events if e.signal == signal]


@pytest.fixture
def event_bus() -> FakeEventBus:
    return FakeEventBus()


@pytest.fixture
def breaker(event_bus: FakeEventBus) -> FederationCircuitBreaker:
    """Breaker with low thresholds so streak boundaries are reachable in tests."""
    return FederationCircuitBreaker(
        failure_threshold=5,
        recovery_timeout_seconds=30.0,
        gateway_timeout_threshold=3,
        gateway_disconnect_threshold=3,
        event_bus=event_bus,
    )


class TestRecordGatewayTimeout:
    """`record_gateway_timeout` — DEGRADED state (coordination only)."""

    @pytest.mark.asyncio
    async def test_below_threshold_does_not_emit(
        self, breaker: FederationCircuitBreaker, event_bus: FakeEventBus
    ) -> None:
        await breaker.record_gateway_timeout("gw-1", error_code="REQUEST_TIMEOUT")
        await breaker.record_gateway_timeout("gw-1", error_code="REQUEST_TIMEOUT")

        assert event_bus.events == []
        assert not breaker.is_gateway_degraded("gw-1")
        assert not breaker.is_gateway_unhealthy("gw-1")

    @pytest.mark.asyncio
    async def test_reaching_threshold_emits_degraded_and_keeps_routing(
        self, breaker: FederationCircuitBreaker, event_bus: FakeEventBus
    ) -> None:
        for _ in range(3):
            await breaker.record_gateway_timeout("gw-1", error_code="REQUEST_TIMEOUT")

        degraded = event_bus.by_signal(FEDERATION_GATEWAY_DEGRADED)
        assert len(degraded) == 1
        assert degraded[0].payload == {
            "gateway_id": "gw-1",
            "consecutive_timeouts": 3,
            "first_error_code": "REQUEST_TIMEOUT",
        }
        assert breaker.is_gateway_degraded("gw-1")
        assert not breaker.is_gateway_unhealthy("gw-1"), (
            "DEGRADED MUST NOT exclude routing — coordination signal only"
        )

    @pytest.mark.asyncio
    async def test_beyond_threshold_does_not_re_emit_degraded(
        self, breaker: FederationCircuitBreaker, event_bus: FakeEventBus
    ) -> None:
        for _ in range(5):
            await breaker.record_gateway_timeout("gw-1", error_code="INFERENCE_TIMEOUT")

        assert len(event_bus.by_signal(FEDERATION_GATEWAY_DEGRADED)) == 1, (
            "DEGRADED is asserted once per streak; further timeouts should not"
            " spam additional events"
        )

    @pytest.mark.asyncio
    async def test_first_error_code_pinned_to_streak_start(
        self, breaker: FederationCircuitBreaker, event_bus: FakeEventBus
    ) -> None:
        await breaker.record_gateway_timeout("gw-1", error_code="LOAD_TIMEOUT")
        await breaker.record_gateway_timeout("gw-1", error_code="REQUEST_TIMEOUT")
        await breaker.record_gateway_timeout("gw-1", error_code="INFERENCE_TIMEOUT")

        degraded = event_bus.by_signal(FEDERATION_GATEWAY_DEGRADED)
        assert degraded[0].payload["first_error_code"] == "LOAD_TIMEOUT"


class TestRecordGatewayDisconnect:
    """`record_gateway_disconnect` — UNHEALTHY state (routing excluded)."""

    @pytest.mark.asyncio
    async def test_below_threshold_does_not_emit(
        self, breaker: FederationCircuitBreaker, event_bus: FakeEventBus
    ) -> None:
        await breaker.record_gateway_disconnect("gw-1", error_code="EDGE_UNREACHABLE")
        await breaker.record_gateway_disconnect("gw-1", error_code="EDGE_UNREACHABLE")

        assert event_bus.events == []
        assert not breaker.is_gateway_unhealthy("gw-1")

    @pytest.mark.asyncio
    async def test_reaching_threshold_emits_unhealthy_and_excludes_routing(
        self, breaker: FederationCircuitBreaker, event_bus: FakeEventBus
    ) -> None:
        for _ in range(3):
            await breaker.record_gateway_disconnect(
                "gw-1", error_code="GATEWAY_DISCONNECTED"
            )

        unhealthy = event_bus.by_signal(FEDERATION_GATEWAY_UNHEALTHY)
        assert len(unhealthy) == 1
        assert unhealthy[0].payload == {
            "gateway_id": "gw-1",
            "consecutive_disconnects": 3,
            "first_error_code": "GATEWAY_DISCONNECTED",
            "cooldown_s": 30.0,
        }
        assert breaker.is_gateway_unhealthy("gw-1")
        assert not await breaker.should_allow_request("gw-1", "model-x"), (
            "UNHEALTHY gateway MUST be excluded from routing for the cooldown"
        )

    @pytest.mark.asyncio
    async def test_beyond_threshold_refreshes_cooldown_without_re_emitting(
        self, breaker: FederationCircuitBreaker, event_bus: FakeEventBus
    ) -> None:
        for _ in range(3):
            await breaker.record_gateway_disconnect(
                "gw-1", error_code="EDGE_UNREACHABLE"
            )
        opened_after_threshold = breaker._gateway_wide_open["gw-1"]

        # Subsequent disconnects inside the cooldown window must extend it.
        # Without timestamp refresh, a still-down gateway becomes routable
        # again at original_open + 30s even while disconnects keep arriving.
        await breaker.record_gateway_disconnect("gw-1", error_code="EDGE_UNREACHABLE")
        opened_after_extra = breaker._gateway_wide_open["gw-1"]

        assert opened_after_extra >= opened_after_threshold
        assert len(event_bus.by_signal(FEDERATION_GATEWAY_UNHEALTHY)) == 1, (
            "UNHEALTHY is asserted once per streak; further disconnects refresh"
            " the cooldown but do not re-emit"
        )


class TestRecordGatewaySuccess:
    """`record_gateway_success` — clears DEGRADED and/or UNHEALTHY."""

    @pytest.mark.asyncio
    async def test_no_op_when_no_streaks(
        self, breaker: FederationCircuitBreaker, event_bus: FakeEventBus
    ) -> None:
        await breaker.record_gateway_success("gw-1")

        assert event_bus.events == []

    @pytest.mark.asyncio
    async def test_clears_degraded_and_emits_recovered_degradation(
        self, breaker: FederationCircuitBreaker, event_bus: FakeEventBus
    ) -> None:
        for _ in range(3):
            await breaker.record_gateway_timeout("gw-1", error_code="REQUEST_TIMEOUT")
        assert breaker.is_gateway_degraded("gw-1")
        event_bus.events.clear()

        await breaker.record_gateway_success("gw-1")

        recovered = event_bus.by_signal(FEDERATION_GATEWAY_RECOVERED)
        assert len(recovered) == 1
        assert recovered[0].payload == {
            "gateway_id": "gw-1",
            "kind": "degradation",
            "reason": "first_success",
        }
        assert not breaker.is_gateway_degraded("gw-1")

    @pytest.mark.asyncio
    async def test_clears_unhealthy_and_emits_recovered_reachability(
        self, breaker: FederationCircuitBreaker, event_bus: FakeEventBus
    ) -> None:
        for _ in range(3):
            await breaker.record_gateway_disconnect(
                "gw-1", error_code="GATEWAY_DISCONNECTED"
            )
        assert breaker.is_gateway_unhealthy("gw-1")
        event_bus.events.clear()

        await breaker.record_gateway_success("gw-1")

        recovered = event_bus.by_signal(FEDERATION_GATEWAY_RECOVERED)
        assert len(recovered) == 1
        assert recovered[0].payload == {
            "gateway_id": "gw-1",
            "kind": "reachability",
            "reason": "probe_succeeded",
        }
        assert not breaker.is_gateway_unhealthy("gw-1"), (
            "successful response on UNHEALTHY gateway MUST clear the cooldown"
        )

    @pytest.mark.asyncio
    async def test_clears_both_states_emits_two_recovered_events(
        self, breaker: FederationCircuitBreaker, event_bus: FakeEventBus
    ) -> None:
        for _ in range(3):
            await breaker.record_gateway_timeout("gw-1", error_code="REQUEST_TIMEOUT")
        for _ in range(3):
            await breaker.record_gateway_disconnect(
                "gw-1", error_code="GATEWAY_DISCONNECTED"
            )
        assert breaker.is_gateway_degraded("gw-1")
        assert breaker.is_gateway_unhealthy("gw-1")
        event_bus.events.clear()

        await breaker.record_gateway_success("gw-1")

        recovered = event_bus.by_signal(FEDERATION_GATEWAY_RECOVERED)
        kinds = {e.payload["kind"] for e in recovered}
        assert kinds == {"degradation", "reachability"}, (
            "both prior assertions must announce recovery independently"
        )
        assert not breaker.is_gateway_degraded("gw-1")
        assert not breaker.is_gateway_unhealthy("gw-1")

    @pytest.mark.asyncio
    async def test_streaks_reset_so_next_failure_starts_fresh(
        self, breaker: FederationCircuitBreaker, event_bus: FakeEventBus
    ) -> None:
        for _ in range(3):
            await breaker.record_gateway_timeout("gw-1", error_code="REQUEST_TIMEOUT")
        await breaker.record_gateway_success("gw-1")
        event_bus.events.clear()

        # Two more timeouts must NOT re-trigger DEGRADED — the streak counter
        # must have reset to 0, requiring a fresh threshold-cross.
        await breaker.record_gateway_timeout("gw-1", error_code="REQUEST_TIMEOUT")
        await breaker.record_gateway_timeout("gw-1", error_code="REQUEST_TIMEOUT")

        assert event_bus.events == []
        assert not breaker.is_gateway_degraded("gw-1")


class TestNoEventBusIsSafe:
    """Health methods MUST not crash when event_bus is None (default)."""

    @pytest.mark.asyncio
    async def test_no_bus_no_crash(self) -> None:
        breaker = FederationCircuitBreaker(
            gateway_timeout_threshold=2,
            gateway_disconnect_threshold=2,
            event_bus=None,
        )
        await breaker.record_gateway_timeout("gw-1", error_code="REQUEST_TIMEOUT")
        await breaker.record_gateway_timeout("gw-1", error_code="REQUEST_TIMEOUT")
        await breaker.record_gateway_disconnect("gw-1", error_code="EDGE_UNREACHABLE")
        await breaker.record_gateway_disconnect("gw-1", error_code="EDGE_UNREACHABLE")
        await breaker.record_gateway_success("gw-1")

        assert not breaker.is_gateway_degraded("gw-1")
        assert not breaker.is_gateway_unhealthy("gw-1")
