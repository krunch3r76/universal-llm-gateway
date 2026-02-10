"""
Gateway tracking — eviction protection, observability, and lifecycle facade.

Admission control: CapacityLedger in systems/routing/capacity/

Provides:
    - GatewayTracker: Combined status + in-flight tracking facade
    - gateway_tracker: Global instance

Responsibilities:
    - Eviction protection: routing_keys with in-flight requests are shielded
    - Observability: per-gateway request counts and capacity-key counters
    - Lifecycle: gateway registration, draining, shutdown, stale-request cleanup

For implementation details, see src.core.gateway/.
"""

import time
from dataclasses import dataclass, field

from universal_logging import get_logger

from .gateway import (
    GatewayStatusRegistry,
    InFlightRequestTracker,
    gateway_status_registry,
    in_flight_tracker,
)

logger = get_logger(__name__)


@dataclass
class GatewayInfo:
    """Information about a gateway instance."""

    gateway_id: str
    host: str
    port: int
    available: bool = True
    draining: bool = False
    last_seen: float = field(default_factory=time.time)
    shutdown_timestamp: float | None = None
    drain_timestamp: float | None = None


class GatewayTracker:
    """
    Unified facade over split gateway tracking components.

    Combines:
        - GatewayStatusRegistry: availability/lifecycle
        - InFlightRequestTracker: in-flight request tracking

    Single-writer assumption: all mutations happen on one async event loop.
    """

    def __init__(
        self,
        status_registry: GatewayStatusRegistry | None = None,
        in_flight_tracker_instance: InFlightRequestTracker | None = None,
    ):
        self._status = status_registry or gateway_status_registry
        self._in_flight = in_flight_tracker_instance or in_flight_tracker
        self._cleanup_task = None  # Background cleanup task

    def register_gateway(self, gateway_id: str, host: str, port: int) -> None:
        """Register a gateway as available."""
        self._status.register_gateway(gateway_id, host, port)
        self._in_flight.register_gateway(gateway_id)

    def mark_draining(self, gateway_id: str, timestamp: float | None = None) -> None:
        """Mark a gateway as draining (unavailable for new requests)."""
        in_flight_count = self._in_flight.get_in_flight_count(gateway_id)
        self._status.mark_draining(gateway_id, timestamp)
        logger.info(f"{in_flight_count} requests may complete on draining {gateway_id}")

    def mark_shutdown(self, gateway_id: str, timestamp: float) -> set[str]:
        """
        Mark a gateway as shut down and return in-flight request IDs.

        Returns:
            Set of request IDs that need retry/reroute
        """
        self._status.mark_shutdown(gateway_id, timestamp)
        affected_requests = self._in_flight.clear_gateway(gateway_id)
        count = len(affected_requests)
        logger.info(f"{gateway_id} shutdown, {count} requests need retry")
        return affected_requests

    def track_request(
        self,
        gateway_id: str,
        request_id: str,
        routing_key: str,
    ) -> None:
        """
        Track that a request is in-flight to a gateway.

        Args:
            gateway_id: Gateway handling the request
            request_id: Unique request identifier
            routing_key: Canonical model identity (normalize_model_id().routing_key)
        """
        self._in_flight.track_request(gateway_id, request_id, routing_key)

    def try_reserve_slot(
        self,
        gateway_id: str,
        endpoint_category: str,
        compute_type: str,
        request_id: str,
        routing_key: str,
        max_concurrent_requests: int = 1,
    ) -> bool:
        """
        Track an in-flight request for a capacity key.

        This tracker is NOT authoritative for admission control. It records counts
        for observability and soft assertions only.

        Args:
            gateway_id: Gateway identifier
            endpoint_category: "generation" or "embedding"
            compute_type: "cpu", "hybrid", or "gpu"
            request_id: Unique request identifier
            routing_key: Canonical model identity (for eviction protection)
            max_concurrent_requests: Soft limit for diagnostics/logging only
        """
        return self._in_flight.try_reserve_slot(
            gateway_id=gateway_id,
            endpoint_category=endpoint_category,
            compute_type=compute_type,
            request_id=request_id,
            routing_key=routing_key,
            max_concurrent_requests=max_concurrent_requests,
        )

    def get_capacity_count(
        self,
        gateway_id: str,
        endpoint_category: str,
        compute_type: str,
    ) -> int:
        """
        Get count of in-flight requests for a capacity key.

        Args:
            gateway_id: Gateway identifier
            endpoint_category: "generation" or "embedding"
            compute_type: "cpu", "hybrid", or "gpu"

        Returns:
            Number of in-flight requests for this capacity key.
        """
        return self._in_flight.get_capacity_count(
            gateway_id, endpoint_category, compute_type
        )

    def complete_request(self, gateway_id: str, request_id: str) -> None:
        """
        Mark a request as completed (no longer in-flight).

        Called by:
        - Direct calls (deterministic release)

        Idempotent: second call for same request_id is no-op.
        """
        self._in_flight.complete_request(gateway_id, request_id)

    def get_routing_keys_in_use(self, gateway_id: str) -> set[str]:
        """
        Get routing_keys with in-flight requests on a gateway.

        Returns:
            Set of routing_keys that have active requests on the gateway.
        """
        return self._in_flight.get_routing_keys_in_use(gateway_id)

    def get_routing_keys_in_use_globally(self) -> set[str]:
        """
        Get routing_keys with in-flight requests across ALL gateways.

        Returns:
            Set of routing_keys that have active requests on any gateway.
            Future-proof for multi-gateway deployments.
        """
        return self._in_flight.get_routing_keys_in_use_globally()

    def get_in_flight_count(self, gateway_id: str) -> int:
        """
        Get count of in-flight requests on a gateway.

        Args:
            gateway_id: Gateway to check

        Returns:
            Number of in-flight requests on the gateway
        """
        return self._in_flight.get_in_flight_count(gateway_id)

    def get_available_gateways(self) -> list[GatewayInfo]:
        """Get list of available gateways for routing (excludes draining)."""
        # Convert internal GatewayInfo to this module's GatewayInfo for BC
        return [
            GatewayInfo(
                gateway_id=g.gateway_id,
                host=g.host,
                port=g.port,
                available=g.available,
                draining=g.draining,
                last_seen=g.last_seen,
                shutdown_timestamp=g.shutdown_timestamp,
                drain_timestamp=g.drain_timestamp,
            )
            for g in self._status.get_available_gateways()
        ]

    def is_available(self, gateway_id: str) -> bool:
        """Check if a specific gateway is available."""
        return self._status.is_available(gateway_id)

    def start_background_cleanup(
        self,
        interval_seconds: int = 60,
        max_age_seconds: int = 600,
    ) -> None:
        """
        Start background cleanup task for stale requests.

        Args:
            interval_seconds: Cleanup check interval (default 60s)
            max_age_seconds: Max request age before cleanup (default 600s = 10min)

        Should be called once at application startup.
        """
        import asyncio

        if self._cleanup_task is not None:
            logger.warning("Background cleanup task already running")
            return

        self._cleanup_task = asyncio.create_task(
            self._in_flight.run_periodic_cleanup(
                interval_seconds=interval_seconds,
                max_age_seconds=max_age_seconds,
            )
        )

        logger.info(
            f"✅ Started background cleanup task "
            f"(interval={interval_seconds}s, max_age={max_age_seconds}s)"
        )

    async def stop_background_cleanup(self) -> None:
        """Stop background cleanup task gracefully."""
        import asyncio

        if self._cleanup_task is None:
            return

        logger.info("Stopping background cleanup task...")
        self._cleanup_task.cancel()

        try:
            await self._cleanup_task
        except asyncio.CancelledError:
            pass

        self._cleanup_task = None
        logger.info("✅ Background cleanup task stopped")


# Global tracker instance (facade over split components)
gateway_tracker = GatewayTracker()
