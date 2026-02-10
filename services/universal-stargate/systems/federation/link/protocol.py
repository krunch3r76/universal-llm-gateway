"""
Telemetry link protocol - transport abstraction for Master↔Remote telemetry ONLY.

SCOPE: Telemetry transport only. Inference/tokens/load use existing HTTP domains.

INVARIANT: HTTP polling only when config.disable_websocket = true
INVARIANT: Inside-node Stargate↔Gateway uses WS (Layer A, not affected by this protocol)
INVARIANT: Per-remote transport selection (mixed fleets supported)

DESIGN NOTE: Uses `emit_*` not `send_*` to avoid implying push semantics.
- For WS: emit = push immediately
- For HTTP polling: emit = accumulate for next poll response
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..common.config.schema import FederationConfig


@dataclass(frozen=True)
class TelemetryUpdate:
    """
    Transport-agnostic telemetry update.

    Semantic Contract:
    - WS: Delivered near-immediately after state change
    - HTTP Polling: Delivered on next poll (up to poll_interval_ms delay)

    Callers MUST NOT assume immediate delivery. For load completion
    confirmation, use FreshnessWaiter.wait_for_telemetry_update().

    Attributes:
        remote_id: Remote Stargate identifier
        gateway_id: Gateway identifier (stable key for state)
        seq: Monotonic sequence number (0 = full snapshot after restart)
        payload: Resource state, loaded models, etc.
        timestamp_ms: Remote's timestamp (for staleness detection)
    """

    remote_id: str
    gateway_id: str
    seq: int
    payload: dict[str, Any]
    timestamp_ms: int

    @property
    def is_snapshot(self) -> bool:
        """True if this is a full snapshot (seq=0 indicates restart)."""
        return self.seq == 0


@runtime_checkable
class TelemetryReceiver(Protocol):
    """
    Master-side: Receives telemetry updates from Remote Stargates.

    Implementations:
    - WSMasterReceiver: Accepts WS connections, receives events
    - HTTPPollingReceiver: Polls remotes on interval

    Both implementations MUST:
    1. Apply updates to gateway_manager (apply_delta/apply_snapshot)
    2. Publish GATEWAY_RESOURCE_UPDATE event (for FreshnessWaiter)
    3. Use bounded queue for backpressure (MAS-01)

    Injected Dependencies (via constructor):
        - gateway_manager: Receives apply_delta()/apply_snapshot() calls
        - event_bus: Publishes GATEWAY_RESOURCE_UPDATE for routing freshness
        - freshness is signaled via event_bus subscription (no direct waiter injection)
    """

    @abstractmethod
    async def start(self) -> None:
        """Start receiving telemetry."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop receiving telemetry."""
        ...

    @abstractmethod
    def is_running(self) -> bool:
        """Check if receiver is active."""
        ...


@runtime_checkable
class TelemetryEmitter(Protocol):
    """
    Remote-side: Emits telemetry updates for transport to Master Stargate.

    Uses `emit_*` not `send_*` to avoid implying push semantics:
    - WS: emit = push immediately via WebSocket
    - HTTP Polling: emit = accumulate for next poll response

    Implementations:
    - WSRemoteEmitter: Pushes via WS connection
    - HTTPPollingEmitter: Accumulates state for poll responses
    """

    @abstractmethod
    async def emit_delta(self, delta: TelemetryUpdate) -> None:
        """
        Emit telemetry delta.

        For WS: Sends immediately.
        For HTTP polling: Accumulates for next poll response.
        """
        ...

    @abstractmethod
    async def emit_snapshot(self, snapshot: TelemetryUpdate) -> None:
        """
        Emit full telemetry snapshot (on connect or restart).

        For WS: Sends immediately.
        For HTTP polling: Sets as response for next poll with ?full=true.
        """
        ...


class MixedFleetTelemetryManager:
    """
    Manages telemetry receivers for mixed fleets (some WS, some polling).

    INVARIANT: Each remote has exactly one transport mechanism
    INVARIANT: WS server shared across all WS remotes; pollers are per-remote

    Architecture:
    - WS remotes: Single shared WSMasterReceiver accepts all connections
    - Polling remotes: One HTTPPollingReceiver per remote

    NOTE: Does NOT use create_receiver_for_remote() factory for WS.
          WS uses shared server; factory only makes sense for polling.
    """

    def __init__(
        self,
        config: FederationConfig,
        gateway_manager: Any,
        event_bus: Any,
    ) -> None:
        self._config = config
        self._gateway_manager = gateway_manager
        self._event_bus = event_bus
        self._pollers: dict[str, TelemetryReceiver] = {}
        self._ws_server: TelemetryReceiver | None = None
        self._running = False

    async def start(self) -> None:
        """Start all receivers (WS server + per-remote pollers)."""
        if self._running:
            return
        self._running = True

        ws_remotes = [r for r in self._config.remotes if not r.disable_websocket]
        polling_remotes = [r for r in self._config.remotes if r.disable_websocket]

        # Single WS server for all WS remotes
        if ws_remotes:
            from .ws.master.server import WSMasterReceiver

            self._ws_server = WSMasterReceiver(
                config=self._config,
                gateway_manager=self._gateway_manager,
                event_bus=self._event_bus,
            )
            await self._ws_server.start()

        # Per-remote pollers for polling remotes
        for remote in polling_remotes:
            from .http_polling.master.poller import HTTPPollingReceiver

            poller = HTTPPollingReceiver(
                remote_config=remote,
                config=self._config,
                gateway_manager=self._gateway_manager,
                event_bus=self._event_bus,
            )
            self._pollers[remote.stargate_id] = poller
            await poller.start()

    async def stop(self) -> None:
        """Stop all receivers."""
        if not self._running:
            return
        self._running = False

        if self._ws_server:
            await self._ws_server.stop()
            self._ws_server = None

        for poller in self._pollers.values():
            await poller.stop()
        self._pollers.clear()

    def is_running(self) -> bool:
        """Check if manager is active."""
        return self._running

    def get_transport_mode(self, remote_id: str) -> str:
        """Get transport mode for a remote (for observability)."""
        if remote_id in self._pollers:
            return "HTTP_POLLING"
        return "WS"
