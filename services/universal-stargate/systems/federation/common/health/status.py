"""
Federation-aware health endpoint.

Returns health status based on federation mode and connectivity.

Health semantics:
- healthy: All systems operational
- degraded: Functional but reduced capacity (some remotes unreachable)
- unhealthy: Cannot function (gateway unreachable in Remote mode)

Health metrics granularity:
- last_pong_age_ms per remote
- pending_cancels_queue_depth
- active_requests_by_stage
"""

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from universal_logging import get_logger

from ..config import FederationConfig, StargateMode

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class RequestTrackerProtocol(Protocol):
    """Protocol for request tracker to avoid accessing private attributes."""

    @property
    def active_count(self) -> int: ...

    @property
    def pending_cancel_count(self) -> int: ...

    def count_active_by_remote(self, remote_id: str) -> int: ...

    def count_pending_cancels_by_remote(self, remote_id: str) -> int: ...


@dataclass(slots=True)
class FederationHealth:
    """Federation health status."""

    status: str  # "healthy", "degraded", "unhealthy"
    mode: str
    stargate_id: str
    uptime_seconds: float
    federation_details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "stargate_id": self.stargate_id,
            "uptime_s": int(self.uptime_seconds),
            "federation": self.federation_details,
        }


@dataclass(slots=True)
class RemoteHealthMetrics:
    """Health metrics for a single remote."""

    stargate_id: str
    connected: bool
    last_pong_age_ms: int
    active_requests: int
    pending_cancels: int


class FederationHealthHandler:
    """
    Health handler for federation.

    Status logic (from spec §10):
    - Standalone: always healthy (if running)
    - Remote: unhealthy if gateway unreachable
    - Master:
      - healthy if all remotes reachable
      - degraded if some (but not all) remotes unreachable
      - degraded if no reachable gateways (still running but limited)

    Exposes granular metrics:
    - last_pong_age_ms per remote (VPS idle timeout awareness)
    - pending_cancels_queue_depth (reconnection health)
    - active_requests_by_stage (request lifecycle visibility)
    """

    def __init__(
        self,
        config: FederationConfig,
        federated_manager: Any | None = None,  # FederatedGatewayManager (Master-only)
        tracker: RequestTrackerProtocol | None = None,
        start_time: float | None = None,
    ):
        self._config = config
        self._federated_manager = federated_manager
        self._tracker = tracker
        self._start_time = start_time or time.time()

        # Per-remote last pong times
        self._last_pong_times: dict[str, float] = {}

        # For Remote mode: track last gateway message
        self._last_gateway_message_time: float = time.time()

    def update_pong_time(self, remote_id: str) -> None:
        """Update last pong time for a remote."""
        self._last_pong_times[remote_id] = time.time()

    def update_gateway_activity(self) -> None:
        """Update last gateway activity time (for Remote mode)."""
        self._last_gateway_message_time = time.time()

    def get_health(self) -> FederationHealth:
        """Get current federation health status."""
        uptime = time.time() - self._start_time

        if self._config.mode == StargateMode.EDGE:
            return FederationHealth(
                status="healthy",
                mode="edge",
                stargate_id=self._config.stargate_id,
                uptime_seconds=uptime,
                federation_details={},
            )

        if self._config.mode == StargateMode.REMOTE:
            return self._get_remote_health(uptime)

        if self._config.mode == StargateMode.MASTER:
            return self._get_master_health(uptime)

        return FederationHealth(
            status="unknown",
            mode=self._config.mode.value,
            stargate_id=self._config.stargate_id,
            uptime_seconds=uptime,
            federation_details={},
        )

    def _get_remote_health(self, uptime: float) -> FederationHealth:
        """Get health for Remote mode."""
        gateway_silence_ms = (time.time() - self._last_gateway_message_time) * 1000
        unreachable_threshold_ms = self._config.telemetry_unreachable_threshold_ms

        status = (
            "unhealthy" if gateway_silence_ms > unreachable_threshold_ms else "healthy"
        )

        return FederationHealth(
            status=status,
            mode="remote",
            stargate_id=self._config.stargate_id,
            uptime_seconds=uptime,
            federation_details={
                "last_gateway_telemetry_ms": int(gateway_silence_ms),
                "connected_masters": [],  # Wire to ws_handler
            },
        )

    def _get_master_health(self, uptime: float) -> FederationHealth:
        """Get health for Master mode."""
        if not self._federated_manager:
            return FederationHealth(
                status="degraded",
                mode="master",
                stargate_id=self._config.stargate_id,
                uptime_seconds=uptime,
                federation_details={"reason": "No federated gateway manager"},
            )

        healthy_gateways = self._federated_manager.get_healthy_gateways()
        all_gateways = self._federated_manager.get_all_gateways()

        # Determine status based on reachability
        if not healthy_gateways:
            status = "degraded"  # No gateways reachable
        elif len(healthy_gateways) < len(all_gateways):
            status = "degraded"  # Partial reachability
        else:
            status = "healthy"  # All gateways reachable

        # Build per-remote metrics
        remote_metrics = self._get_remote_metrics()

        # Get tracker stats via public interface
        tracker_stats = {}
        if self._tracker:
            tracker_stats = {
                "active_requests": self._tracker.active_count,
                "pending_cancels": self._tracker.pending_cancel_count,
            }

        return FederationHealth(
            status=status,
            mode="master",
            stargate_id=self._config.stargate_id,
            uptime_seconds=uptime,
            federation_details={
                "connected_remotes": [g.remote_stargate_id for g in all_gateways],
                "healthy_gateways": len(healthy_gateways),
                "total_gateways": len(all_gateways),
                "remote_metrics": remote_metrics,
                **tracker_stats,
            },
        )

    def _get_remote_metrics(self) -> list[dict[str, Any]]:
        """Get per-remote health metrics using public tracker interface."""
        remote_health_metrics = []
        now = time.time()

        for remote_id, last_pong in self._last_pong_times.items():
            pong_age_ms = int((now - last_pong) * 1000)

            # Use public methods to query tracker
            active_count = 0
            pending_count = 0
            if self._tracker:
                active_count = self._tracker.count_active_by_remote(remote_id)
                pending_count = self._tracker.count_pending_cancels_by_remote(remote_id)

            remote_health_metrics.append(
                {
                    "stargate_id": remote_id,
                    "last_pong_age_ms": pong_age_ms,
                    "active_requests": active_count,
                    "pending_cancels": pending_count,
                }
            )

        return remote_health_metrics

    def get_http_status_code(self) -> int:
        """Get HTTP status code for health check."""
        health = self.get_health()

        if health.status == "healthy":
            return 200
        elif health.status == "degraded":
            return 200  # Still returns 200 per spec
        else:
            return 503


def get_federation_health(
    config: FederationConfig,
    federated_manager: Any | None = None,  # FederatedGatewayManager (Master-only)
) -> dict[str, Any]:
    """
    Get federation health for integration with /healthz.

    Used by systems/proxy/routers/health.py.
    """
    handler = FederationHealthHandler(config, federated_manager)
    return handler.get_health().to_dict()
