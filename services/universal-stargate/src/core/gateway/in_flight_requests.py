"""
In-flight request tracking for eviction protection and observability.

Design:
    - Tracks by routing_key (canonical model identity) for eviction protection
    - Tracks by (gateway, endpoint_category, compute_type) for observability counters
    - Handles variant suffixes (-hybrid, -cpu, context lengths) correctly
    - Eviction filtering compares routing_keys, avoiding mismatch bugs

Admission control: CapacityLedger in systems/routing/capacity/
Counters here are for observability, diagnostics, and soft assertions only.
"""

import time

from universal_logging import get_logger

logger = get_logger(__name__)

# Type alias for capacity key
CapacityKey = tuple[str, str, str]  # (gateway_id, endpoint_category, compute_type)


class InFlightRequestTracker:
    """
    Tracks in-flight requests for eviction protection and observability.

    Invariant: ∀ request_id ∈ _request_routing_keys ⟹
               ∃ gateway_id: request_id ∈ _in_flight_requests[gateway_id]
    Single-writer assumption: all mutations happen on one async event loop.
    No thread synchronization provided.
    """

    def __init__(self):
        self._in_flight_requests: dict[str, set[str]] = {}  # gateway_id -> request_ids
        # Request→gateway mapping to ensure deterministic cleanup even if callers
        # provide a mismatched gateway_id (e.g., federated routing event mismatch).
        self._gateway_by_request_id: dict[str, str] = {}
        self._request_routing_keys: dict[str, str] = {}  # request_id -> routing_key
        # Per-capacity-key tracking (for slot reservation)
        self._request_capacity_keys: dict[str, CapacityKey] = {}  # request_id -> key
        self._inflight_by_capacity_key: dict[CapacityKey, int] = {}
        # Track request age for cleanup
        self._request_timestamps: dict[str, float] = {}  # request_id -> start_time

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
        capacity_key: CapacityKey = (gateway_id, endpoint_category, compute_type)
        current = self._inflight_by_capacity_key.get(capacity_key, 0)

        if current >= max_concurrent_requests:
            logger.debug(
                f"⚠️ Soft capacity exceeded: {endpoint_category}/{compute_type} "
                f"on {gateway_id} ({current}/{max_concurrent_requests}), "
                f"tracking request {request_id[:8]}"
            )

        # Record slot usage (non-gating).
        self._inflight_by_capacity_key[capacity_key] = current + 1

        # Track the request (for eviction protection)
        if gateway_id not in self._in_flight_requests:
            self._in_flight_requests[gateway_id] = set()
        self._in_flight_requests[gateway_id].add(request_id)
        self._gateway_by_request_id[request_id] = gateway_id
        self._request_routing_keys[request_id] = routing_key
        self._request_capacity_keys[request_id] = capacity_key
        self._request_timestamps[request_id] = time.time()

        logger.debug(
            f"✅ Slot tracked: {endpoint_category}/{compute_type} on {gateway_id} - "
            f"count={current + 1}, request={request_id[:8]}"
        )
        return True

    def register_gateway(self, gateway_id: str) -> None:
        """Initialize tracking for a gateway."""
        if gateway_id not in self._in_flight_requests:
            self._in_flight_requests[gateway_id] = set()

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
        if gateway_id not in self._in_flight_requests:
            self._in_flight_requests[gateway_id] = set()

        self._in_flight_requests[gateway_id].add(request_id)
        self._gateway_by_request_id[request_id] = gateway_id
        self._request_routing_keys[request_id] = routing_key

        # Record timestamp for age tracking
        self._request_timestamps[request_id] = time.time()

        logger.debug(
            f"📍 Tracking request {request_id[:8]} on {gateway_id} "
            f"(routing_key={routing_key})"
        )

    def complete_request(self, gateway_id: str, request_id: str) -> None:
        """
        Mark a request as completed and release tracked counters.

        Idempotent: safe to call multiple times for the same request.
        """
        capacity_key = self._request_capacity_keys.pop(request_id, None)
        tracked_gateway_id = self._gateway_by_request_id.pop(request_id, None)

        # Fast-path idempotency: nothing tracked for this request_id.
        if (
            capacity_key is None
            and tracked_gateway_id is None
            and request_id not in self._request_routing_keys
            and request_id not in self._request_timestamps
        ):
            return

        # Prefer stored gateway mapping; fall back to capacity_key or caller hint.
        effective_gateway_id = tracked_gateway_id or (
            capacity_key[0] if capacity_key is not None else gateway_id
        )

        if effective_gateway_id in self._in_flight_requests:
            self._in_flight_requests[effective_gateway_id].discard(request_id)

        self._request_routing_keys.pop(request_id, None)
        self._request_timestamps.pop(request_id, None)

        # Release observability counter
        if capacity_key is not None:
            if capacity_key in self._inflight_by_capacity_key:
                old_count = self._inflight_by_capacity_key[capacity_key]
                new_count = max(0, old_count - 1)
                if new_count == 0:
                    del self._inflight_by_capacity_key[capacity_key]
                else:
                    self._inflight_by_capacity_key[capacity_key] = new_count
                logger.debug(
                    f"🔓 Slot RELEASED: {capacity_key[1]}/{capacity_key[2]} "
                    f"on {capacity_key[0]} - count={old_count}→{new_count}, "
                    f"request={request_id[:8]}"
                )
            else:
                logger.warning(
                    f"⚠️ Slot release for unknown capacity key: {capacity_key}, "
                    f"request={request_id[:8]} (may have been cleaned up)"
                )

    def get_routing_keys_in_use(self, gateway_id: str) -> set[str]:
        """
        Get routing_keys with in-flight requests on a gateway.

        Returns:
            Set of routing_keys that have active requests on the gateway.
            Used by eviction to protect models from being unloaded mid-request.
        """
        request_ids = self._in_flight_requests.get(gateway_id, set())
        routing_keys = {
            self._request_routing_keys[rid]
            for rid in request_ids
            if rid in self._request_routing_keys
        }
        return routing_keys

    def clear_gateway(self, gateway_id: str) -> set[str]:
        """
        Clear all in-flight requests for a gateway (e.g., on shutdown).

        Returns:
            Set of request IDs that were cleared (for retry logic).
        """
        affected_requests = self._in_flight_requests.get(gateway_id, set()).copy()

        # Clean up all mappings for affected requests
        for rid in affected_requests:
            self._request_routing_keys.pop(rid, None)
            self._request_capacity_keys.pop(rid, None)
            self._gateway_by_request_id.pop(rid, None)
            self._request_timestamps.pop(rid, None)

        # Clear capacity counters for this gateway
        keys_to_remove = [
            k for k in self._inflight_by_capacity_key if k[0] == gateway_id
        ]
        for key in keys_to_remove:
            del self._inflight_by_capacity_key[key]

        self._in_flight_requests[gateway_id] = set()

        logger.info(f"Cleared {len(affected_requests)} in-flight for {gateway_id}")
        return affected_requests

    def get_in_flight_count(self, gateway_id: str) -> int:
        """Get count of in-flight requests on a gateway."""
        return len(self._in_flight_requests.get(gateway_id, set()))

    def get_routing_keys_in_use_globally(self) -> set[str]:
        """
        Get routing_keys with in-flight requests across ALL gateways.

        Returns:
            Set of routing_keys that have active requests on any gateway.
            Used by eviction to protect models from being unloaded when
            the same routing_key is in use on another gateway.

        Future-proof: Works for both single-gateway (current) and
        multi-gateway (future) deployments.
        """
        all_routing_keys = set()
        for gateway_id in self._in_flight_requests:
            all_routing_keys |= self.get_routing_keys_in_use(gateway_id)
        return all_routing_keys

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
        key: CapacityKey = (gateway_id, endpoint_category, compute_type)
        return self._inflight_by_capacity_key.get(key, 0)

    def cleanup_stale_requests(self, max_age_seconds: int = 600) -> int:
        """
        Remove requests older than max_age_seconds (default 10 minutes).

        This is a safety net for missed complete_request() calls. Normal requests
        should complete within seconds/minutes; 10+ minutes indicates orphaned state.

        Args:
            max_age_seconds: Maximum age before considering request stale

        Returns:
            Number of stale requests cleaned up

        Invariant: ∀ r ∈ cleaned: age(r) > max_age_seconds
        """
        import time

        now = time.time()
        stale_requests: list[tuple[str, str]] = []  # (gateway_id, request_id)

        # Find stale requests across all gateways
        for gateway_id, request_ids in self._in_flight_requests.items():
            for request_id in list(
                request_ids
            ):  # Copy to avoid modification during iteration
                timestamp = self._request_timestamps.get(request_id)
                if timestamp is None:
                    # No timestamp (shouldn't happen, but defensive)
                    logger.warning(
                        f"⚠️ Request {request_id[:8]} on {gateway_id} has no timestamp, "
                        f"cleaning up"
                    )
                    stale_requests.append((gateway_id, request_id))
                elif now - timestamp > max_age_seconds:
                    age_minutes = (now - timestamp) / 60
                    threshold_min = max_age_seconds / 60
                    logger.warning(
                        f"🧹 Found stale request {request_id[:8]} on {gateway_id} "
                        f"(age={age_minutes:.1f}min, threshold={threshold_min:.1f}min)"
                    )
                    stale_requests.append((gateway_id, request_id))

        # Clean up stale requests
        for gateway_id, request_id in stale_requests:
            routing_key = self._request_routing_keys.get(request_id, "unknown")
            logger.warning(
                f"🧹 Cleaning up stale request {request_id[:8]} on {gateway_id} "
                f"(routing_key={routing_key})"
            )
            self.complete_request(gateway_id, request_id)

        if stale_requests:
            logger.warning(
                f"🧹 Cleaned up {len(stale_requests)} stale requests "
                f"(threshold={max_age_seconds}s)"
            )

        return len(stale_requests)

    async def run_periodic_cleanup(
        self,
        interval_seconds: int = 60,
        max_age_seconds: int = 600,
    ) -> None:
        """
        Run periodic cleanup task in background.

        Args:
            interval_seconds: How often to run cleanup (default 60s)
            max_age_seconds: Max age before considering stale (default 600s)

        Usage:
            # Start in background
            asyncio.create_task(tracker.run_periodic_cleanup())
        """
        import asyncio

        logger.info(
            f"🧹 Starting periodic request cleanup "
            f"(interval={interval_seconds}s, max_age={max_age_seconds}s)"
        )

        while True:
            try:
                await asyncio.sleep(interval_seconds)

                # Run cleanup
                cleaned = self.cleanup_stale_requests(max_age_seconds)

                if cleaned > 0:
                    logger.warning(
                        f"🧹 Periodic cleanup removed {cleaned} stale requests"
                    )
                else:
                    logger.debug("🧹 Periodic cleanup: no stale requests found")

            except asyncio.CancelledError:
                logger.info("🧹 Periodic cleanup task cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Error in periodic cleanup: {e}", exc_info=True)
                # Continue running despite errors


# Global tracker instance
in_flight_tracker = InFlightRequestTracker()
