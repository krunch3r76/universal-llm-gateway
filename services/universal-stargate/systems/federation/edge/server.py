"""
Edge Federation Server - Accepts inbound telemetry connections.

INVARIANT: ∀ telemetry from Gateway: cached ∧ forwarded_to_connected_peers
INVARIANT: ∀ peer connection: authenticated_before_telemetry

Pattern: Mirror of MasterWebSocketServer but for Edge mode.
Edge is passive - accepts connections, doesn't initiate them.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Any

from starlette.websockets import WebSocketDisconnect
from universal_event_bus import EventBus
from universal_logging import get_logger
from universal_protocol.messages import TelemetrySource

from ..common.config import FederationConfig
from ..common.protocol import (
    FederationMessageType,
    create_federation_auth_result,
    create_measurement_vram_request,
)
from .telemetry import (
    build_initial_telemetry_payload,
    create_model_lifecycle_callbacks,
    create_periodic_heartbeat_task,
    create_periodic_snapshot_task,
    create_resource_update_callback,
)

if TYPE_CHECKING:
    from fastapi import WebSocket

    from gateway_websocket.ws_client.orchestrator import GatewayWebSocketClient

logger = get_logger(__name__)

VRAM_REQUEST_TIMEOUT = 10.0


class EdgeFederationServer:
    """
    Edge Federation Server for accepting inbound telemetry connections.

    Responsibilities:
    1. Accept inbound WebSocket connections from Master/Relay
    2. Authenticate peers against allowed_peers config
    3. Cache telemetry from local Gateway
    4. Push telemetry to connected peers

    Architecture (from TOPOLOGY.md):
      Gateway → Edge Stargate (local WS)
        → EdgeFederationServer (cache)
          ← Master/Relay (inbound WS to /ws/federation/edge)
          → Push telemetry to connected peers

    INVARIANT: Edge never initiates outbound federation connections.
    """

    def __init__(
        self,
        config: FederationConfig,
        gateway_manager: object,
        event_bus: EventBus | None,
    ):
        """
        Initialize Edge Federation Server.

        Args:
            config: Federation configuration (mode=EDGE)
            gateway_manager: Gateway manager for local Gateway access
            event_bus: Event bus for emitting federation lifecycle signals
        """
        self._config = config
        self._gateway_manager = gateway_manager
        self._event_bus: EventBus | None = event_bus

        # Build allowed peers lookup for O(1) auth
        self._allowed_peers: dict[str, str] = {}  # stargate_id → api_key
        for peer in config.allowed_peers or []:
            self._allowed_peers[peer.stargate_id] = peer.api_key

        # Telemetry source (identifies this Edge in telemetry)
        # Gateway ID format: "{stargate_id}-gateway" for global uniqueness
        # Example: "edge-jupiter" → "edge-jupiter-gateway"
        self._source = TelemetrySource(
            stargate_id=config.stargate_id,
            gateway_id=f"{config.stargate_id}-gateway",
            node_id=config.node_id,
        )

        # Telemetry cache for late-joining peers
        self._cached_resource_update: dict[str, Any] | None = None
        self._cached_gateway_snapshot: dict[str, Any] | None = None

        # Connected and authenticated peers
        self._authenticated_peers: dict[str, WebSocket] = {}  # stargate_id → ws
        # Peers authenticated before GATEWAY_SNAPSHOT exists (startup timing race)
        self._pending_snapshot_peers: set[str] = set()

        # Periodic heartbeat task (for preventing telemetry staleness)
        self._heartbeat_task: asyncio.Task[None] | None = None
        # Periodic snapshot task (for federation state reconciliation)
        self._snapshot_task: asyncio.Task[None] | None = None

        # Pending measurement requests awaiting response from Master
        self._pending_requests: dict[str, asyncio.Future[dict[str, Any]]] = {}

        if self._event_bus is not None:
            self._event_bus.subscribe_async(
                "request.inference.started",
                self._forward_request_inference_started,
            )

        logger.info(
            f"EdgeFederationServer initialized for {config.stargate_id} "
            f"(allowed_peers={list(self._allowed_peers.keys())})"
        )

    # ─── Peer Authentication ───────────────────────────────────────────────

    async def authenticate_peer(
        self, websocket: WebSocket, auth_data: dict[str, Any]
    ) -> bool:
        """
        Authenticate inbound peer connection.

        Args:
            websocket: WebSocket connection
            auth_data: Auth payload from peer

        Returns:
            True if authenticated, False otherwise
        """
        peer_id = auth_data.get("stargate_id", "")
        api_key = auth_data.get("api_key", "")

        # Trusted topology — accept any peer without credential checks
        if not self._config.federation_auth_enabled:
            logger.info(f"Auth disabled — accepting peer {peer_id}")
        else:
            expected_key = self._allowed_peers.get(peer_id)
            if not expected_key:
                logger.warning(f"Auth failed: {peer_id} not in allowed_peers")
                await self._send_auth_result(websocket, False, "Unknown peer")
                if self._event_bus is not None:
                    from src.scheduling.events.federation_signaling import (
                        FederationPeerAuthFailed,
                    )

                    asyncio.create_task(
                        self._event_bus.publish_nowait(
                            FederationPeerAuthFailed(
                                peer_id=peer_id,
                                reason="unknown_peer",
                            )
                        ),
                        name="emit-federation-peer-auth-failed",
                    )
                return False

            if api_key != expected_key:
                logger.warning(f"Auth failed: Invalid API key for {peer_id}")
                await self._send_auth_result(websocket, False, "Invalid API key")
                if self._event_bus is not None:
                    from src.scheduling.events.federation_signaling import (
                        FederationPeerAuthFailed,
                    )

                    asyncio.create_task(
                        self._event_bus.publish_nowait(
                            FederationPeerAuthFailed(
                                peer_id=peer_id,
                                reason="invalid_api_key",
                            )
                        ),
                        name="emit-federation-peer-auth-failed",
                    )
                return False

        await self._send_auth_result(
            websocket,
            True,
            "Authenticated",
            stargate_id=self._config.stargate_id,
        )

        # Register peer only after auth acknowledgement is successfully sent.
        self._authenticated_peers[peer_id] = websocket

        logger.info(f"✅ Peer {peer_id} authenticated")
        if self._event_bus is not None:
            from src.scheduling.events.federation_signaling import (
                FederationConnectionAuthenticated,
            )

            asyncio.create_task(
                self._event_bus.publish_nowait(
                    FederationConnectionAuthenticated(
                        remote_id=peer_id,
                        method=(
                            "disabled"
                            if not self._config.federation_auth_enabled
                            else "api_key"
                        ),
                    )
                ),
                name="emit-federation-connection-authenticated",
            )

        # Send cached GATEWAY_SNAPSHOT to new peer (contains catalog for routing).
        # If not ready yet, queue for delivery when telemetry wiring completes.
        if self._cached_gateway_snapshot:
            await self._send_cached_telemetry(websocket, peer_id)
        else:
            self._pending_snapshot_peers.add(peer_id)
            logger.info(
                f"📋 Peer {peer_id} authenticated before GATEWAY_SNAPSHOT ready "
                "— queued for snapshot delivery"
            )

        return True

    async def _send_auth_result(
        self,
        websocket: WebSocket,
        success: bool,
        message: str,
        stargate_id: str | None = None,
    ) -> None:
        """Send auth result to peer."""
        msg = create_federation_auth_result(
            success=success,
            message=message,
            stargate_id=stargate_id,
            protocol_version="1.0",
        )
        await websocket.send_text(json.dumps(msg.to_dict()))

    async def _send_cached_telemetry(self, websocket: WebSocket, peer_id: str) -> None:
        """Send cached GATEWAY_SNAPSHOT to newly connected peer."""
        if not self._cached_gateway_snapshot:
            return

        try:
            await websocket.send_text(json.dumps(self._cached_gateway_snapshot))
            data = self._cached_gateway_snapshot.get("data", {})
            model_count = len(data.get("available_models", []))
            logger.info(
                f"📤 Sent GATEWAY_SNAPSHOT to {peer_id}: {model_count} available models"
            )
        except WebSocketDisconnect:
            logger.info(f"Peer {peer_id} disconnected while sending cached telemetry")
            asyncio.create_task(self.handle_peer_disconnect(peer_id))
        except Exception as e:
            logger.error(
                f"Failed to send cached telemetry to {peer_id}: "
                f"{e.__class__.__name__} - {e}"
            )

    # ─── Peer Lifecycle ────────────────────────────────────────────────────

    async def handle_peer_disconnect(self, peer_id: str) -> None:
        """Remove disconnected peers from auth and pending snapshot registries."""
        self._pending_snapshot_peers.discard(peer_id)
        if peer_id in self._authenticated_peers:
            del self._authenticated_peers[peer_id]
            remaining = len(self._authenticated_peers)
            logger.info(f"⚠️ Peer {peer_id} disconnected (remaining: {remaining})")
            if self._event_bus is not None:
                from src.scheduling.events.federation_signaling import (
                    FederationPeerDisconnected,
                )

                asyncio.create_task(
                    self._event_bus.publish_nowait(
                        FederationPeerDisconnected(
                            peer_id=peer_id,
                            remaining_peers=remaining,
                        )
                    ),
                    name="emit-federation-peer-disconnected",
                )

    # ─── Telemetry Caching & Forwarding ────────────────────────────────────

    def _update_cached_snapshot_model_state(
        self,
        msg_type: str,
        model_id: str,
    ) -> None:
        """
        Update cached GATEWAY_SNAPSHOT with model lifecycle change.

        INVARIANT: cached_snapshot.loaded_models = gateway.loaded_models

        This ensures late-joining peers receive current state, not stale snapshot.

        Args:
            msg_type: Message type (e.g., "telemetry.model.loaded")
            model_id: Model identifier
        """
        if not self._cached_gateway_snapshot:
            return

        snapshot_data = self._cached_gateway_snapshot.get("data", {})

        # Convert to sets for efficient manipulation
        loaded_models = set(snapshot_data.get("loaded_models", []))
        busy_models = set(snapshot_data.get("busy_models", []))
        loading_models = set(snapshot_data.get("loading_models", []))

        # Apply state change based on event type
        if msg_type == FederationMessageType.MODEL_LOADED.value:
            loaded_models.add(model_id)
            loading_models.discard(model_id)
            logger.debug(f"📊 Cache updated: +loaded {model_id}")

        elif msg_type == FederationMessageType.MODEL_UNLOADED.value:
            loaded_models.discard(model_id)
            busy_models.discard(model_id)
            loading_models.discard(model_id)
            logger.debug(f"📊 Cache updated: -loaded {model_id}")

        elif msg_type == FederationMessageType.MODEL_BUSY.value:
            busy_models.add(model_id)
            logger.debug(f"📊 Cache updated: +busy {model_id}")

        elif msg_type == FederationMessageType.MODEL_IDLE.value:
            busy_models.discard(model_id)
            logger.debug(f"📊 Cache updated: -busy {model_id}")

        elif msg_type == FederationMessageType.MODEL_LOADING_STARTED.value:
            loading_models.add(model_id)
            logger.debug(f"📊 Cache updated: +loading {model_id}")

        else:
            # Not a model lifecycle event
            return

        # Write back to snapshot
        snapshot_data["loaded_models"] = list(loaded_models)
        snapshot_data["busy_models"] = list(busy_models)
        snapshot_data["loading_models"] = list(loading_models)

    async def cache_and_forward_telemetry(
        self, msg_type: str, data: dict[str, Any]
    ) -> None:
        """
        Cache telemetry and forward to all connected peers.

        Called by gateway_websocket callbacks when Gateway sends telemetry.

        Args:
            msg_type: Message type (e.g., "telemetry.resource.updated")
            data: Telemetry payload
        """
        # Add source to telemetry
        data_with_source = {
            **data,
            "source": self._source.to_dict(),
        }

        message = {
            "type": msg_type,
            "data": data_with_source,
        }

        # Cache resource updates for forwarding
        # Late-joiners get GATEWAY_SNAPSHOT instead
        if msg_type == FederationMessageType.RESOURCE_UPDATE.value:
            self._cached_resource_update = message
            model_count = len(data.get("loaded_models", []))
            logger.debug(f"📊 Cached RESOURCE_UPDATE: {model_count} loaded models")

        # Update cached snapshot for model lifecycle events
        # This ensures late-joining peers receive current state
        # (atomic — _update_cached_snapshot_model_state is synchronous, no await)
        model_id = data.get("model_id")
        if model_id:
            self._update_cached_snapshot_model_state(msg_type, model_id)

        # Broadcast to all authenticated peers
        await self._broadcast_to_peers(message)

    async def _broadcast_to_peers(self, message: dict[str, Any]) -> None:
        """Broadcast message to all authenticated peers."""
        if not self._authenticated_peers:
            # Log at INFO for visibility (cache still updated, just no live peers)
            msg_type = message.get("type", "unknown")
            if "model" in msg_type.lower():
                logger.info(
                    f"📭 No peers connected for {msg_type} broadcast "
                    f"(cache updated, will be sent on peer connect)"
                )
            return

        message_json = json.dumps(message)

        # Snapshot to avoid modification during iteration
        # (atomic — list() on dict is synchronous, no await)
        peers = list(self._authenticated_peers.items())

        for peer_id, websocket in peers:
            try:
                await websocket.send_text(message_json)
            except WebSocketDisconnect:
                logger.info(f"Peer {peer_id} disconnected during broadcast")
                asyncio.create_task(self.handle_peer_disconnect(peer_id))
            except Exception as e:
                logger.error(
                    f"Failed to send to {peer_id}: {e.__class__.__name__} - {e}"
                )
                # Will be cleaned up on next disconnect handler call

    # ─── Measurement Request/Response ──────────────────────────────────────

    async def request_vram_snapshot(self, device_index: int = 0) -> dict[str, Any]:
        """Send VRAM measurement request to Master, await response.

        Raises TimeoutError if no response within VRAM_REQUEST_TIMEOUT.
        Raises RuntimeError if no peers connected.
        """
        if not self._authenticated_peers:
            raise RuntimeError("No peers connected for VRAM measurement")

        request_id = uuid.uuid4().hex[:12]
        msg = create_measurement_vram_request(request_id, device_index)
        msg_json = json.dumps(msg.to_dict())

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_requests[request_id] = future

        try:
            # Snapshot peers (atomic — no await between snapshot and check)
            peers = list(self._authenticated_peers.items())
            if not peers:
                raise RuntimeError("No peers connected for VRAM measurement")
            sent = False
            for peer_id, websocket in peers:
                try:
                    await websocket.send_text(msg_json)
                    sent = True
                    if self._event_bus is not None:
                        from src.scheduling.events.federation_signaling import (
                            FederationVramRequestSent,
                        )

                        asyncio.create_task(
                            self._event_bus.publish_nowait(
                                FederationVramRequestSent(
                                    request_id=request_id,
                                    peer_id=peer_id,
                                    device_index=device_index,
                                )
                            ),
                            name="emit-federation-vram-request-sent",
                        )
                    break
                except WebSocketDisconnect:
                    logger.info(
                        "Peer %s disconnected during VRAM request dispatch", peer_id
                    )
                    asyncio.create_task(self.handle_peer_disconnect(peer_id))
                except Exception as e:
                    logger.error(
                        "Failed to send VRAM request to %s: %s - %s",
                        peer_id,
                        e.__class__.__name__,
                        e,
                    )
            if not sent:
                if self._event_bus is not None:
                    from src.scheduling.events.federation_signaling import (
                        FederationVramRequestFailed,
                    )

                    asyncio.create_task(
                        self._event_bus.publish_nowait(
                            FederationVramRequestFailed(
                                request_id=request_id,
                                reason="no_peer_dispatch_succeeded",
                            )
                        ),
                        name="emit-federation-vram-request-failed",
                    )
                raise RuntimeError(
                    "Failed to dispatch VRAM measurement request to peers"
                )

            return await asyncio.wait_for(future, timeout=VRAM_REQUEST_TIMEOUT)
        finally:
            self._pending_requests.pop(request_id, None)

    def resolve_measurement_response(self, data: dict[str, Any]) -> bool:
        """Resolve a pending measurement Future. Returns True if matched."""
        request_id = data.get("request_id", "")
        future = self._pending_requests.get(request_id)
        if future and not future.done():
            future.set_result(data)
            if self._event_bus is not None:
                from src.scheduling.events.federation_signaling import (
                    FederationVramResponseReceived,
                )

                asyncio.create_task(
                    self._event_bus.publish_nowait(
                        FederationVramResponseReceived(
                            request_id=request_id,
                            matched=True,
                        )
                    ),
                    name="emit-federation-vram-response-received",
                )
            return True
        logger.warning(f"No pending request for measurement response {request_id}")
        if self._event_bus is not None:
            from src.scheduling.events.federation_signaling import (
                FederationVramResponseReceived,
            )

            asyncio.create_task(
                self._event_bus.publish_nowait(
                    FederationVramResponseReceived(
                        request_id=request_id,
                        matched=False,
                    )
                ),
                name="emit-federation-vram-response-unmatched",
            )
        return False

    # ─── Gateway Telemetry Wiring ──────────────────────────────────────────

    def wire_gateway_telemetry(
        self, ws_client: GatewayWebSocketClient, gateway_url: str
    ) -> None:
        """
        Wire local Gateway WebSocket callbacks to Edge telemetry forwarding.

        CRITICAL: This enables telemetry flow from local Gateway → Edge → Master.

        Pattern: Follows Remote mode's wiring (remote/telemetry/wiring.py)
        Available callbacks on GatewayWebSocketClient:
          - on_resource_update(callback: Callable[[dict], Awaitable[None]])
          - on_model_loaded(callback: Callable[[str, dict], Awaitable[None]])
          - on_model_unloaded(callback: Callable[[str], Awaitable[None]])

        NOT available (don't wire):
          - on_init_received (internal method, not a callback setter)

        Args:
            ws_client: Gateway WebSocket client (GatewayWebSocketClient)
            gateway_url: URL of the local gateway
        """
        # NOTE: Do NOT overwrite self._source here
        # Gateway ID already set in __init__ as "{stargate_id}-gateway"
        # Using gateway_url would cause collision across edges

        # Wire resource update callback
        on_resource_update = create_resource_update_callback(
            ws_client, self.cache_and_forward_telemetry
        )
        ws_client.on_resource_update(on_resource_update)

        # Wire model lifecycle callbacks
        lifecycle_callbacks = create_model_lifecycle_callbacks(
            self.cache_and_forward_telemetry
        )
        ws_client.on_model_loading_started(
            lifecycle_callbacks["on_model_loading_started"]
        )
        ws_client.on_model_loading_progress(
            lifecycle_callbacks["on_model_loading_progress"]
        )
        ws_client.on_model_loaded(lifecycle_callbacks["on_model_loaded"])
        ws_client.on_model_load_failed(lifecycle_callbacks["on_model_load_failed"])
        ws_client.on_model_unloaded(lifecycle_callbacks["on_model_unloaded"])
        ws_client.on_model_busy(lifecycle_callbacks["on_model_busy"])
        ws_client.on_model_idle(lifecycle_callbacks["on_model_idle"])

        # ─── Initial Telemetry ──────────────────────────────────────────────
        # Send initial snapshot immediately (INIT is already processed by ws_client)

        self._send_initial_telemetry(ws_client, gateway_url)

        # ─── Periodic Heartbeat ─────────────────────────────────────────────
        # Start periodic heartbeat to prevent telemetry staleness
        self._start_periodic_heartbeat(ws_client)

        # ─── Periodic Snapshot ─────────────────────────────────────────────
        # Reconcile Master's federation state (loaded_models, resources)
        self._start_periodic_snapshot(ws_client)

        logger.info(
            f"✅ Gateway telemetry wired for Edge forwarding (gateway={gateway_url})"
        )
        if self._event_bus is not None:
            from src.scheduling.events.federation_signaling import (
                FederationTelemetryWired,
            )

            asyncio.create_task(
                self._event_bus.publish_nowait(
                    FederationTelemetryWired(
                        gateway_url=gateway_url,
                        gateway_id=self._source.gateway_id,
                    )
                ),
                name="emit-federation-telemetry-wired",
            )

    def _send_initial_telemetry(
        self, ws_client: GatewayWebSocketClient, gateway_url: str
    ) -> None:
        """
        Build and cache GATEWAY_SNAPSHOT, then deliver to all peers.

        Handles startup timing where peers can authenticate before gateway telemetry
        wiring completes. Pending peers are drained first, then all authenticated
        peers receive the same cached snapshot idempotently.

        PHASE 2: Includes available_models + model_resources for Master routing.
        Subsequent RESOURCE_UPDATEs only send loaded/busy state (not catalog).

        In relay topology, the relay connects to Master BEFORE Edge connects to
        Gateway, so we must broadcast immediately to already-connected peers.

        Args:
            ws_client: Gateway WebSocket client
            gateway_url: Gateway URL (for logging)
        """
        # Build payload using extracted helper
        payload = build_initial_telemetry_payload(ws_client, self._source)

        # Cache GATEWAY_SNAPSHOT for late-joining peers (contains catalog)
        self._cached_gateway_snapshot = {
            "type": FederationMessageType.GATEWAY_SNAPSHOT.value,
            "data": payload,
        }

        model_count = len(payload["available_models"])
        resource_count = len(payload.get("model_resources", {}))
        all_models_count = len(
            list(ws_client.get_models()) if hasattr(ws_client, "get_models") else []
        )
        vram = payload["available_vram_mb"]
        ram = payload["available_ram_mb"]
        logger.info(
            f"📊 Initial telemetry cached: {model_count} routable models "
            f"({all_models_count} in catalog, {resource_count} with resources), "
            f"VRAM: {vram}MB, RAM: {ram}MB"
        )

        from src.scheduling.events import FederationSnapshotSent

        if self._event_bus is not None:
            asyncio.create_task(
                self._event_bus.publish_nowait(
                    FederationSnapshotSent(
                        gateway_id=self._source.gateway_id,
                        all_models_count=all_models_count,
                        available_models_count=model_count,
                    )
                ),
                name="emit-federation-snapshot-sent",
            )

        # Deliver snapshot to connected peers with explicit per-peer handling.
        asyncio.create_task(
            self._deliver_snapshot_to_all_peers(),
            name="initial-telemetry-delivery",
        )

    async def _deliver_snapshot_to_all_peers(self) -> None:
        """Deliver cached snapshot to pending and currently authenticated peers."""
        if not self._cached_gateway_snapshot:
            return

        pending = tuple(self._pending_snapshot_peers)
        self._pending_snapshot_peers.clear()

        if pending:
            logger.info(
                f"📤 Delivering GATEWAY_SNAPSHOT to {len(pending)} pending peer(s): "
                f"{list(pending)}"
            )

        snapshot_json = json.dumps(self._cached_gateway_snapshot)
        peers = list(self._authenticated_peers.items())
        delivered = 0

        for peer_id, websocket in peers:
            try:
                await websocket.send_text(snapshot_json)
                delivered += 1
                logger.info(
                    f"📤 GATEWAY_SNAPSHOT delivered to {peer_id} "
                    f"({'was pending' if peer_id in pending else 'already connected'})"
                )
            except WebSocketDisconnect:
                logger.info(f"Peer {peer_id} disconnected during snapshot delivery")
                asyncio.create_task(self.handle_peer_disconnect(peer_id))
            except Exception as e:
                logger.error(
                    f"Failed to deliver GATEWAY_SNAPSHOT to {peer_id}: "
                    f"{e.__class__.__name__} - {e}"
                )

        logger.info(
            f"📊 GATEWAY_SNAPSHOT delivery complete: "
            f"{delivered}/{len(peers)} peers served"
        )

    def _start_periodic_heartbeat(self, ws_client: GatewayWebSocketClient) -> None:
        """
        Start periodic heartbeat task to prevent telemetry staleness.

        Args:
            ws_client: Gateway WebSocket client (for connection status check)
        """
        self._heartbeat_task = create_periodic_heartbeat_task(
            ws_client=ws_client,
            stargate_id=self._config.stargate_id,
            gateway_id=self._source.gateway_id,
            node_id=self._config.node_id,
            broadcast_callback=self._broadcast_to_peers,
        )

    def _start_periodic_snapshot(self, ws_client: GatewayWebSocketClient) -> None:
        """
        Start periodic GATEWAY_SNAPSHOT task for federation state reconciliation.

        Heals Master's loaded_models view after disconnect/reconnect cycles where
        the gateway was recreated and discrete MODEL_LOADED events were lost.

        Interval from config (snapshot_interval_ms). 0 = disabled.
        """
        interval_ms = self._config.snapshot_interval_ms
        if interval_ms <= 0:
            logger.info(
                "📊 Periodic GATEWAY_SNAPSHOT disabled (snapshot_interval_ms=0)"
            )
            return

        interval_s = interval_ms / 1000.0

        self._snapshot_task = create_periodic_snapshot_task(
            ws_client=ws_client,
            source=self._source,
            interval_s=interval_s,
            refresh_callback=self._apply_periodic_snapshot,
        )

    async def _apply_periodic_snapshot(self, payload: dict[str, Any]) -> None:
        """
        Replace cached GATEWAY_SNAPSHOT and broadcast to all peers.

        Atomic overwrite (not merge) — ensures stale models don't persist.
        """
        self._cached_gateway_snapshot = {
            "type": FederationMessageType.GATEWAY_SNAPSHOT.value,
            "data": payload,
        }

        await self._broadcast_to_peers(self._cached_gateway_snapshot)

        loaded_count = len(payload.get("loaded_models", []))
        model_count = len(payload.get("available_models", []))
        logger.info(
            f"📊 Periodic GATEWAY_SNAPSHOT broadcast: "
            f"{model_count} available, {loaded_count} loaded"
        )

        if self._event_bus is not None:
            from src.scheduling.events import FederationSnapshotSent

            asyncio.create_task(
                self._event_bus.publish_nowait(
                    FederationSnapshotSent(
                        gateway_id=self._source.gateway_id,
                        all_models_count=model_count,
                        available_models_count=model_count,
                        trigger="periodic",
                    )
                ),
                name="emit-federation-periodic-snapshot-sent",
            )

    async def _forward_request_inference_started(self, event: Any) -> None:
        """Forward request.inference.started to connected Master peers.

        Transient per-request telemetry — not cached (unlike model/resource state).
        """
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            return

        message: dict[str, Any] = {
            "type": FederationMessageType.REQUEST_INFERENCE_STARTED.value,
            "data": payload,
        }
        await self._broadcast_to_peers(message)
        if self._event_bus is not None:
            from src.scheduling.events.federation_signaling import (
                FederationRequestInferenceStartedForwarded,
            )

            await self._event_bus.publish_nowait(
                FederationRequestInferenceStartedForwarded(
                    request_id=payload.get("request_id"),
                    peer_count=len(self._authenticated_peers),
                )
            )
