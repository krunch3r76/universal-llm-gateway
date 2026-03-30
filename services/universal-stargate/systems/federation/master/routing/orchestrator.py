"""
Master-side request forwarding orchestration.

Wires tracking with FederatedRequestForwarder for proper cancellation.

INVARIANT: register BEFORE first_await in forwarding
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from universal_logging import get_logger

from ...common.config.schema import EndpointCategory
from ...common.types import FederatedGateway, RequestState, TrackedRequest
from .forward import FederatedRequestForwarder

logger = get_logger(__name__)


class MasterRequestTracker:
    """
    Tracks outbound requests from Master for cancellation.

    INVARIANT:
      register(r) BEFORE first_await(r.forward)
      ∀ terminal_state: idempotent transitions

    Usage:
        tracker = MasterRequestTracker(forwarder, cancel_sender)

        # Forward with tracking
        async for chunk in tracker.forward_stream(gateway, body, hop):
            yield chunk
    """

    def __init__(
        self,
        forwarder: FederatedRequestForwarder,
        send_cancel: Callable[[str, str], Awaitable[bool]],
    ):
        """
        Args:
            forwarder: Phase 4 forwarder for HTTP requests
            send_cancel: Callback(remote_stargate_id, request_id) -> success
                request_id is sent as X-Correlation-ID header for wire compatibility
        """
        self._forwarder = forwarder
        self._send_cancel = send_cancel

        # Active requests by request_id (proxy request ID)
        self._active: dict[str, TrackedRequest] = {}

        # Pending cancels by remote_stargate_id (for reconnect replay)
        # Stores request_id (sent to Remote as X-Correlation-ID header
        # for wire compatibility)
        self._pending_cancels: dict[str, list[str]] = {}

        # Routing key tracking for eviction protection
        # Maps request_id -> (gateway_id, routing_key)
        self._routing_key_by_request: dict[str, tuple[str, str]] = {}
        # Maps gateway_id -> set of routing_keys (for fast lookup)
        self._routing_keys_by_gateway: dict[str, set[str]] = {}

        # Cancel groups: group_id → set of request_ids
        # A cancel group is a lifecycle boundary (e.g., one map iteration).
        # cancel_group(group_id) cancels all member requests.
        self._cancel_groups: dict[str, set[str]] = {}

    @property
    def active_count(self) -> int:
        """Count of active requests."""
        return len(self._active)

    @property
    def pending_cancel_count(self) -> int:
        """Count of pending cancellations across all remotes."""
        return sum(len(cancels) for cancels in self._pending_cancels.values())

    @property
    def cancel_group_count(self) -> int:
        """Count of active cancel groups."""
        return len(self._cancel_groups)

    def count_active_by_remote(self, remote_id: str) -> int:
        """Count active requests for a specific remote."""
        return sum(1 for t in self._active.values() if t.remote_id == remote_id)

    def count_pending_cancels_by_remote(self, remote_id: str) -> int:
        """Count pending cancels for a specific remote."""
        return len(self._pending_cancels.get(remote_id, []))

    # ========== ROUTING KEY TRACKING (Eviction Protection) ==========

    def track_routing_key(
        self,
        gateway_id: str,
        request_id: str,
        routing_key: str,
    ) -> None:
        """
        Track routing_key for eviction protection.

        MUST be called BEFORE any await in the load/request path.
        Prevents the model from being evicted while request is in-flight.

        Args:
            gateway_id: Gateway identifier (e.g., "edge-localhost-gateway")
            request_id: Unique request/correlation identifier
            routing_key: Model routing key (for eviction protection)
        """
        self._routing_key_by_request[request_id] = (gateway_id, routing_key)

        if gateway_id not in self._routing_keys_by_gateway:
            self._routing_keys_by_gateway[gateway_id] = set()
        self._routing_keys_by_gateway[gateway_id].add(routing_key)

        logger.debug(
            "🔒 Routing key tracked: %s on %s (request=%s)",
            routing_key,
            gateway_id,
            request_id[:8],
        )

    def release_routing_key(self, request_id: str) -> bool:
        """
        Release routing_key tracking when request completes.

        Idempotent - safe to call multiple times.

        Args:
            request_id: Request ID passed to track_routing_key()

        Returns:
            True if found and released, False if already released
        """
        entry = self._routing_key_by_request.pop(request_id, None)
        if entry is None:
            return False

        gateway_id, routing_key = entry

        # Check if any other request is using this routing_key on this gateway
        still_in_use = any(
            gw_id == gateway_id and rk == routing_key
            for gw_id, rk in self._routing_key_by_request.values()
        )

        if not still_in_use:
            gateway_keys = self._routing_keys_by_gateway.get(gateway_id)
            if gateway_keys:
                gateway_keys.discard(routing_key)
                if not gateway_keys:
                    del self._routing_keys_by_gateway[gateway_id]

        logger.debug(
            "🔓 Routing key released: %s on %s (request=%s, still_in_use=%s)",
            routing_key,
            gateway_id,
            request_id[:8],
            still_in_use,
        )

        return True

    def get_routing_keys_in_flight(self, gateway_id: str) -> set[str]:
        """
        Get routing_keys with in-flight requests on a specific gateway.

        Args:
            gateway_id: Gateway identifier (e.g., "edge-localhost-gateway")

        Returns:
            Set of routing_keys with active requests on this gateway.
        """
        return self._routing_keys_by_gateway.get(gateway_id, set()).copy()

    def get_routing_keys_in_flight_globally(self) -> set[str]:
        """
        Get routing_keys with in-flight requests across ALL gateways.

        Returns:
            Set of routing_keys with active requests on any gateway.
        """
        all_keys: set[str] = set()
        for keys in self._routing_keys_by_gateway.values():
            all_keys |= keys
        return all_keys

    async def forward_stream(
        self,
        gateway: FederatedGateway,
        request_body: dict[str, Any],
        hop_count: int,
        endpoint_category: EndpointCategory,
        model_id: str,
        hints: dict[str, Any] | None = None,
        request_id: str | None = None,
        *,
        cancel_group: str | None = None,
    ) -> AsyncIterator[bytes]:
        """
        Forward streaming request to federated gateway.

        Args:
            gateway: Target federated gateway
            request_body: Request payload to forward
            hop_count: Federation hop count
            endpoint_category: EndpointCategory enum
            model_id: Model identifier
            hints: Optional forwarding hints
            request_id: Optional proxy request ID (generates UUID if None)

        Yields:
            Raw SSE bytes from remote gateway
        """
        endpoint_category_normalized = endpoint_category.value
        if request_id is None:
            request_id = str(uuid.uuid4())
        remote_request_id = str(uuid.uuid4())

        tracked = TrackedRequest(
            request_id=request_id,
            remote_id=gateway.remote_stargate_id,
            remote_request_id=remote_request_id,
            endpoint_category=endpoint_category_normalized,
            compute_type="",
        )
        self._active[request_id] = tracked
        if cancel_group:
            self.register_cancel_group(cancel_group, request_id)

        logger.debug(
            f"Registered outbound request {request_id[:8]}... -> {gateway.gateway_id}"
        )

        try:
            # Now safe to await - request is tracked
            async for chunk in self._forwarder.forward_request_stream(
                gateway, request_body, hop_count, request_id, hints=hints
            ):
                # Check for cancellation
                if tracked.state == RequestState.CANCELLED:
                    logger.info(f"Stream cancelled by tracker: {request_id[:8]}...")
                    break
                yield chunk

            # Mark completed
            tracked.state = RequestState.COMPLETED

        except asyncio.CancelledError:
            # Propagate cancel to remote
            await self._try_cancel(tracked)
            raise
        except Exception:
            tracked.state = RequestState.COMPLETED
            raise
        finally:
            self._active.pop(request_id, None)
            self.release_routing_key(request_id)
            self._remove_from_cancel_groups(request_id)

    async def forward(
        self,
        gateway: FederatedGateway,
        request_body: dict[str, Any],
        hop_count: int,
        endpoint_category: EndpointCategory,
        model_id: str,
        hints: dict[str, Any] | None = None,
        request_id: str | None = None,
        *,
        cancel_group: str | None = None,
    ) -> dict[str, Any]:
        """
        Forward non-streaming request to federated gateway.

        Args:
            gateway: Target federated gateway
            request_body: Request payload to forward
            hop_count: Federation hop count
            endpoint_category: EndpointCategory enum
            model_id: Model identifier
            hints: Optional forwarding hints
            request_id: Optional proxy request ID (generates UUID if None)

        Returns:
            Response dict from remote gateway (parsed JSON)
        """
        endpoint_category_normalized = endpoint_category.value
        if request_id is None:
            request_id = str(uuid.uuid4())
        remote_request_id = str(uuid.uuid4())

        tracked = TrackedRequest(
            request_id=request_id,
            remote_id=gateway.remote_stargate_id,
            remote_request_id=remote_request_id,
            endpoint_category=endpoint_category_normalized,
            compute_type="",
        )
        self._active[request_id] = tracked
        if cancel_group:
            self.register_cancel_group(cancel_group, request_id)

        try:
            response = await self._forwarder.forward_request(
                gateway, request_body, hop_count, request_id, hints=hints
            )
            tracked.state = RequestState.COMPLETED
            return response.json()

        except asyncio.CancelledError:
            await self._try_cancel(tracked)
            raise
        except Exception:
            tracked.state = RequestState.COMPLETED
            raise
        finally:
            self._active.pop(request_id, None)
            self.release_routing_key(request_id)
            self._remove_from_cancel_groups(request_id)

    async def cancel(self, request_id: str) -> bool:
        """
        Cancel an active request by proxy request ID.

        Args:
            request_id: Proxy request ID to cancel

        Returns:
            True if cancelled or already terminal
        """
        tracked = self._active.get(request_id)
        if tracked is None:
            logger.warning(
                f"Cancel requested for {request_id[:8]}... but not in active "
                f"tracking (may be already terminal or ID mismatch). "
                f"Active count: {len(self._active)}"
            )
            return True  # Treat as already terminal
        if tracked.state != RequestState.ACTIVE:
            return True  # Already terminal

        return await self._try_cancel(tracked)

    async def forward_embedding(
        self,
        gateway: FederatedGateway,
        request_body: dict[str, Any],
        model_id: str,
        request_id: str | None = None,
        *,
        cancel_group: str | None = None,
    ) -> dict[str, Any]:
        """
        Forward embedding request to federated gateway.

        Args:
            gateway: Target federated gateway
            request_body: Embedding request payload (model, input)
            model_id: Model identifier
            request_id: Optional proxy request ID (generates UUID if None)

        Returns:
            Embedding response dict from remote gateway
        """
        from ...common.config.schema import EndpointCategory

        if request_id is None:
            request_id = str(uuid.uuid4())
        remote_request_id = str(uuid.uuid4())
        endpoint_category_normalized = EndpointCategory.EMBEDDING.value

        tracked = TrackedRequest(
            request_id=request_id,
            remote_id=gateway.remote_stargate_id,
            remote_request_id=remote_request_id,
            endpoint_category=endpoint_category_normalized,
            compute_type="",
        )
        self._active[request_id] = tracked
        if cancel_group:
            self.register_cancel_group(cancel_group, request_id)

        try:
            # Call embedding-specific forwarder method
            response = await self._forwarder.forward_embedding_request(
                gateway, request_body, request_id
            )
            tracked.state = RequestState.COMPLETED
            return response

        except asyncio.CancelledError:
            await self._try_cancel(tracked)
            raise
        except Exception:
            tracked.state = RequestState.COMPLETED
            raise
        finally:
            self._active.pop(request_id, None)
            self.release_routing_key(request_id)
            self._remove_from_cancel_groups(request_id)

    async def forward_rerank(
        self,
        gateway: FederatedGateway,
        request_body: dict[str, Any],
        model_id: str,
        request_id: str | None = None,
        *,
        cancel_group: str | None = None,
    ) -> dict[str, Any]:
        """
        Forward rerank request to federated gateway.

        Same tracking lifecycle as `forward_embedding`.
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
        remote_request_id = str(uuid.uuid4())
        endpoint_category_normalized = EndpointCategory.RERANK.value

        tracked = TrackedRequest(
            request_id=request_id,
            remote_id=gateway.remote_stargate_id,
            remote_request_id=remote_request_id,
            endpoint_category=endpoint_category_normalized,
            compute_type="",
        )
        self._active[request_id] = tracked
        if cancel_group:
            self.register_cancel_group(cancel_group, request_id)

        try:
            response = await self._forwarder.forward_rerank_request(
                gateway, request_body, request_id
            )
            tracked.state = RequestState.COMPLETED
            return response

        except asyncio.CancelledError:
            await self._try_cancel(tracked)
            raise
        except Exception:
            tracked.state = RequestState.COMPLETED
            raise
        finally:
            self._active.pop(request_id, None)
            self.release_routing_key(request_id)
            self._remove_from_cancel_groups(request_id)

    def register_cancel_group(self, group_id: str, request_id: str) -> None:
        """Register request_id under a cancel group.

        Called when X-Pipeline-Cancel-Group header is present.
        A single request belongs to at most one group.
        """
        self._cancel_groups.setdefault(group_id, set()).add(request_id)
        logger.debug("Cancel group %s: registered %s", group_id[:8], request_id[:8])

    async def cancel_group(self, group_id: str) -> int:
        """Cancel all requests in a cancel group.

        Returns count of successfully cancelled requests.
        Removes the group after processing.
        """
        request_ids = self._cancel_groups.pop(group_id, set())
        if not request_ids:
            logger.debug("Cancel group %s: not found or empty", group_id[:8])
            return 0

        logger.info(
            "Cancel group %s: cancelling %d request(s)", group_id[:8], len(request_ids)
        )
        cancelled = 0
        for rid in request_ids:
            if await self.cancel(rid):
                cancelled += 1
        return cancelled

    def _remove_from_cancel_groups(self, request_id: str) -> None:
        """Remove a completed request from any cancel group it belongs to."""
        for group_id, members in list(self._cancel_groups.items()):
            members.discard(request_id)
            if not members:
                del self._cancel_groups[group_id]

    async def _try_cancel(self, tracked: TrackedRequest) -> bool:
        """
        Try to cancel, queue for retry on failure.

        Cloud gateways (remote_id starts with ``cloud-``) are stateless
        HTTP APIs — there is no remote peer to receive a cancel message.
        We mark the request cancelled locally and return immediately.

        Note: Sends request_id to Remote as X-Correlation-ID header
        for wire compatibility.
        """
        tracked.state = RequestState.CANCELLED

        if tracked.remote_id.startswith("cloud-"):
            logger.debug(
                "Cloud request %s: cancel is local-only (no remote peer)",
                tracked.request_id[:8],
            )
            return True

        try:
            success = await self._send_cancel(
                tracked.remote_id,
                tracked.request_id,
            )

            if success:
                logger.info(f"Cancelled remote request {tracked.request_id[:8]}...")
            return success

        except Exception as e:
            logger.warning(f"Cancel failed, queuing for retry: {e}")

            remote_id = tracked.remote_id
            if remote_id not in self._pending_cancels:
                self._pending_cancels[remote_id] = []
            self._pending_cancels[remote_id].append(tracked.request_id)

            return False

    async def on_remote_reconnect(self, remote_stargate_id: str) -> None:
        """
        Replay pending cancellations on reconnect.

        Called when WebSocket reconnects to a remote.
        Sends pending request_ids as X-Correlation-ID headers for wire compatibility.
        """
        pending = self._pending_cancels.pop(remote_stargate_id, [])

        if pending:
            logger.info(
                f"Replaying {len(pending)} pending cancels for {remote_stargate_id}"
            )

        for request_id in pending:
            try:
                await self._send_cancel(remote_stargate_id, request_id)
            except Exception as e:
                logger.warning(f"Replay cancel failed: {e}")
