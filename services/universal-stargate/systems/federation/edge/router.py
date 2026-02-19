"""
Edge WebSocket router for Master/Relay inbound connections.

Exposes /ws/federation/edge endpoint for telemetry push.

Pattern: Mirrors /ws/federation/master endpoint structure.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from universal_logging import get_logger

from ..common.protocol import FederationMessageType, is_telemetry_type

if TYPE_CHECKING:
    from .server import EdgeFederationServer

logger = get_logger(__name__)


def create_edge_federation_router(edge_server: EdgeFederationServer) -> APIRouter:
    """
    Create Edge federation router with WebSocket endpoint.

    Args:
        edge_server: EdgeFederationServer instance for telemetry forwarding

    Returns:
        APIRouter with /ws/federation/edge endpoint
    """
    router = APIRouter(tags=["federation-edge"])

    @router.websocket("/ws/federation/edge")
    async def edge_websocket(websocket: WebSocket):
        """
        WebSocket endpoint for Master/Relay to connect TO Edge.

        Flow (from TOPOLOGY.md):
        1. Master/Relay connects via Unix socket or HTTP
        2. Edge authenticates peer (check allowed_peers)
        3. Edge sends cached telemetry (RESOURCE_UPDATE)
        4. Edge continues pushing telemetry updates

        Protocol:
        1. Client sends: FederationAuth message
        2. Server responds: FederationAuthResult
        3. If success: Server pushes telemetry
        4. Client may send: FederationPing (keepalive)
        5. Server responds: FederationPong
        """
        await websocket.accept()

        peer_id: str | None = None
        authenticated = False

        logger.info("📥 Inbound WebSocket connection to /ws/federation/edge")

        try:
            # Auth phase - wait for FederationAuth message
            auth_timeout = 5.0  # seconds
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=auth_timeout,
                )
                msg = json.loads(raw)
            except TimeoutError:
                logger.warning("Auth timeout - closing connection")
                await websocket.close(code=4001, reason="Auth timeout")
                return
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON during auth: {e}")
                await websocket.close(code=4002, reason="Invalid JSON")
                return

            # Validate auth message type
            msg_type = msg.get("type", "")
            if msg_type != FederationMessageType.FEDERATION_AUTH.value:
                logger.warning(f"Expected federation_auth, got {msg_type}")
                await websocket.close(code=4003, reason="Expected auth message")
                return

            # Authenticate peer
            auth_data = msg.get("data", {})
            peer_id = auth_data.get("stargate_id", "unknown")

            authenticated = await edge_server.authenticate_peer(websocket, auth_data)
            if not authenticated:
                await websocket.close(code=4004, reason="Auth failed")
                return

            logger.info(f"✅ Peer {peer_id} authenticated - starting telemetry push")

            # Message loop - handle pings, control messages
            while True:
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                msg_type = msg.get("type", "")

                if msg_type == FederationMessageType.FEDERATION_PING.value:
                    pong = {
                        "type": FederationMessageType.FEDERATION_PONG.value,
                        "data": {},
                    }
                    await websocket.send_text(json.dumps(pong))
                    logger.debug(f"Pong sent to {peer_id}")

                elif msg_type == FederationMessageType.MEASUREMENT_VRAM_RESPONSE.value:
                    edge_server.resolve_measurement_response(msg.get("data", {}))

                elif is_telemetry_type(msg_type):
                    logger.debug(f"Received telemetry from {peer_id}: {msg_type}")

                else:
                    logger.debug(f"Unknown message from {peer_id}: {msg_type}")

        except WebSocketDisconnect:
            logger.info(f"Peer {peer_id or 'unknown'} disconnected from Edge")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from {peer_id}: {e}")
        except Exception as e:
            logger.error(f"Edge WebSocket error: {e}", exc_info=True)
        finally:
            # Cleanup peer registration
            if peer_id and authenticated:
                await edge_server.handle_peer_disconnect(peer_id)

    return router
