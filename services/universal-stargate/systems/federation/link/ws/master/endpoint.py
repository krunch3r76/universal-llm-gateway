"""
Master WebSocket endpoint for federation.

Accepts connections from Remote Stargates at /ws/federation/master.
"""

import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from universal_logging import get_logger

from ....common.protocol import (
    FederationMessageType,
    create_federation_pong,
    parse_federation_message,
)
from .server import MasterWebSocketServer

logger = get_logger(__name__)


async def _receive_loop(
    ws: WebSocket,
    remote_id: str,
    server: MasterWebSocketServer,
) -> None:
    """
    Receive loop for authenticated Remote connection.

    Handles:
    - Application-level ping/pong
    - Telemetry messages
    """
    logger.debug(f"Started receive loop for {remote_id}")
    while True:
        raw = await ws.receive_text()
        logger.debug(f"Received raw message from {remote_id}: {raw[:200]}")

        try:
            msg_dict = json.loads(raw)
            msg = parse_federation_message(msg_dict)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Invalid message from {remote_id}: {e}")
            continue

        msg_type = msg.type
        data = msg.data
        logger.debug(
            f"Message from {remote_id}: type={msg_type}, keys={list(data.keys())}"
        )

        if msg_type == FederationMessageType.FEDERATION_PING.value:
            pong = create_federation_pong()
            await ws.send_json(pong.to_dict())
            logger.debug(f"Sent pong to {remote_id}")
            continue

        # Dispatch telemetry to receiver
        logger.debug(f"Dispatching to telemetry receiver: {remote_id}, {msg_type}")
        await server.telemetry_receiver.handle_message(remote_id, msg_type, data)


def create_master_ws_router(
    server: MasterWebSocketServer, event_bus: Any | None = None
) -> APIRouter:
    """Create Master WebSocket router."""
    router = APIRouter(tags=["federation"])

    @router.websocket("/ws/federation/master")
    async def master_websocket(ws: WebSocket):
        """
        Master WebSocket endpoint.

        Lifecycle:
        1. Accept connection
        2. Authenticate Remote within deadline (5s)
        3. Run receive loop (telemetry + ping/pong)
        4. Cleanup on disconnect
        """
        await ws.accept()
        remote_id: str | None = None

        try:
            # Authenticate
            remote_id = await server.auth_handler.authenticate(ws)
            if not remote_id:
                return  # Auth failed, connection already closed

            # Emit authenticated event
            if event_bus:
                import asyncio

                from src.scheduling.events import FederationConnectionAuthenticated

                asyncio.create_task(
                    event_bus.publish_nowait(
                        FederationConnectionAuthenticated(
                            remote_id=remote_id,
                            method="websocket",
                        )
                    )
                )

            # Notify server
            await server.handle_peer_connected(remote_id)

            # Run receive loop
            await _receive_loop(ws, remote_id, server)

        except WebSocketDisconnect:
            logger.info(f"Remote {remote_id or 'unknown'} disconnected")

        except Exception as e:
            logger.error(f"Master WS error: {e}", exc_info=True)

        finally:
            if remote_id:
                await server.handle_peer_disconnected(remote_id)
                logger.info(f"Remote {remote_id} cleaned up")

    return router
