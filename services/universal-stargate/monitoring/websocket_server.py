"""
WebSocket server for real-time gateway state updates.

Provides WebSocket endpoint for live monitoring dashboard updates.
"""

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect
from universal_logging import get_logger

logger = get_logger(__name__)


class WebSocketManager:
    """
    Manages WebSocket connections for real-time monitoring updates.

    Provides:
    - Connection management for multiple clients
    - Real-time state change broadcasting
    - Automatic cleanup of disconnected clients
    """

    def __init__(self, monitoring_consumer):
        """
        Initialize WebSocket manager.

        Args:
            monitoring_consumer: MonitoringConsumer instance for state updates
        """
        self.monitoring_consumer = monitoring_consumer
        self.active_connections: set[WebSocket] = set()
        self._broadcast_task: asyncio.Task = None

    async def connect(self, websocket: WebSocket):
        """
        Accept a new WebSocket connection.

        Args:
            websocket: WebSocket connection to accept
        """
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(
            f"WebSocket client connected (total: {len(self.active_connections)})"
        )

        # Send initial state
        try:
            initial_state = self.monitoring_consumer.get_current_states()
            await websocket.send_json(
                {"type": "initial_state", "states": initial_state}
            )
        except Exception as e:
            logger.error(f"Error sending initial state: {e}")

    def disconnect(self, websocket: WebSocket):
        """
        Remove a disconnected WebSocket connection.

        Args:
            websocket: WebSocket connection to remove
        """
        self.active_connections.discard(websocket)
        logger.info(
            f"WebSocket client disconnected (total: {len(self.active_connections)})"
        )

    async def broadcast(self, message: dict):
        """
        Broadcast a message to all connected clients.

        Args:
            message: Message dictionary to broadcast
        """
        if not self.active_connections:
            return

        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except WebSocketDisconnect:
                disconnected.append(connection)
            except Exception as e:
                logger.error(f"Error broadcasting to client: {e}")
                disconnected.append(connection)

        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)

    async def start_broadcasting(self):
        """Start the broadcasting task that forwards monitoring events"""
        if self._broadcast_task is not None:
            return

        # Subscribe to monitoring consumer updates
        update_queue = await self.monitoring_consumer.subscribe_websocket()

        async def broadcast_loop():
            """Background task that broadcasts monitoring updates"""
            try:
                while True:
                    # Get next update from monitoring consumer
                    update = await update_queue.get()

                    # Broadcast to all connected clients
                    await self.broadcast(update)
            except asyncio.CancelledError:
                logger.info("WebSocket broadcast loop cancelled")
            except Exception as e:
                logger.error(f"Error in broadcast loop: {e}")

        self._broadcast_task = asyncio.create_task(broadcast_loop())
        logger.info("WebSocket broadcasting started")

    async def stop_broadcasting(self):
        """Stop the broadcasting task"""
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None
            logger.info("WebSocket broadcasting stopped")

    async def handle_client(self, websocket: WebSocket):
        """
        Handle a client connection lifecycle.

        Args:
            websocket: WebSocket connection to handle
        """
        await self.connect(websocket)

        try:
            # Keep connection alive and handle client messages
            while True:
                # Receive messages from client
                data = await websocket.receive_text()

                try:
                    message = json.loads(data)
                    await self._handle_client_message(websocket, message)
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {"type": "error", "message": "Invalid JSON"}
                    )
                except Exception as e:
                    logger.error(f"Error handling client message: {e}")
                    await websocket.send_json({"type": "error", "message": str(e)})

        except WebSocketDisconnect:
            self.disconnect(websocket)
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            self.disconnect(websocket)

    async def _handle_client_message(self, websocket: WebSocket, message: dict):
        """
        Handle messages from client.

        Args:
            websocket: WebSocket connection
            message: Message dictionary from client
        """
        message_type = message.get("type")

        if message_type == "ping":
            # Respond to ping
            await websocket.send_json({"type": "pong"})

        elif message_type == "get_state":
            # Send current state
            current_states = self.monitoring_consumer.get_current_states()
            await websocket.send_json(
                {"type": "current_state", "states": current_states}
            )

        elif message_type == "get_history":
            # Send state history
            limit = message.get("limit", 100)
            history = self.monitoring_consumer.get_state_history(limit=limit)
            await websocket.send_json({"type": "history", "transitions": history})

        else:
            # Unknown message type
            await websocket.send_json(
                {"type": "error", "message": f"Unknown message type: {message_type}"}
            )


async def websocket_endpoint(websocket: WebSocket, ws_manager: WebSocketManager):
    """
    WebSocket endpoint handler.

    Args:
        websocket: WebSocket connection
        ws_manager: WebSocketManager instance
    """
    await ws_manager.handle_client(websocket)
