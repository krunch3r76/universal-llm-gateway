"""WebSocket endpoint for real-time state streaming."""

import json
import time

from fastapi import APIRouter, Header, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from universal_logging import get_logger
from universal_protocol.state_channel import StateDelta, StateProtocol, StateUpdate
from universal_protocol.state_channel.protocol import MessageType

from ...core.metrics.state_channel_metrics import state_channel_metrics
from ...core.resources import resource_tracker
from ...middleware.auth import RATE_LIMIT_TIERS, APIKeyInfo, WebSocketAuthenticator
from ...middleware.rate_limiter import websocket_rate_limiter

logger = get_logger(__name__)
router = APIRouter()

# Security components - will be initialized on first use
_authenticator: WebSocketAuthenticator | None = None


def get_authenticator() -> WebSocketAuthenticator:
    """Get or create the authenticator instance."""
    global _authenticator
    if _authenticator is None:
        logger.info("Initializing WebSocketAuthenticator...")
        _authenticator = WebSocketAuthenticator()
        logger.info("WebSocketAuthenticator initialized")
    return _authenticator


class StateChannelHandler:
    """Server-side state channel handler for WebSocket connections."""

    def __init__(
        self,
        websocket: WebSocket,
        state_provider,
        auth_info: APIKeyInfo | None = None,
    ):
        self.websocket = websocket
        self._state_provider = state_provider
        self.subscriptions = set()  # Subscription patterns
        self.version = 0
        self.auth_info = auth_info or APIKeyInfo(
            key_id="anonymous", key_hash="", permissions=set(), rate_limit_tier="basic"
        )
        # Get client identifier for rate limiting
        self.client_id = auth_info.key_id if auth_info else str(websocket.client)

    async def send_state_update(self, update: StateUpdate):
        """Send a state update to the client if it matches subscriptions."""
        # Check if client has subscribed to this path
        if not self._matches_subscriptions(update.path):
            return

        # Encode and send update
        message = StateProtocol.encode_update(update)
        message_text = json.dumps(message)
        await self._send_json(message)

        # Track metrics
        await state_channel_metrics.on_message_sent(
            self.client_id, "update", len(message_text.encode("utf-8"))
        )

    async def send_state_delta(self, delta: StateDelta):
        """Send a state delta to the client if it matches subscriptions."""
        # Check if client has subscribed to this path
        if not self._matches_subscriptions(delta.path):
            return

        # Encode and send delta
        message = StateProtocol.encode_delta(delta)
        message_text = json.dumps(message)
        await self._send_json(message)

        # Track metrics
        await state_channel_metrics.on_message_sent(
            self.client_id, "delta", len(message_text.encode("utf-8"))
        )

    async def send_full_state(self):
        """Send full state sync to client."""
        logger.info("send_full_state: Getting full state snapshot")
        state = await self._state_provider()
        logger.info(f"send_full_state: Got state: {state}")

        message = {
            "type": MessageType.SYNC_RESPONSE.value,
            "state": state,
            "version": self.version,
            "timestamp": time.time(),
        }

        logger.info(f"send_full_state: Sending message: {message}")
        await self._send_json(message)
        logger.info("send_full_state: Message sent")

    async def handle_message(self, data: dict, message_size: int):
        """Handle incoming message from client."""
        # Track incoming message
        await state_channel_metrics.on_message_received(self.client_id, message_size)

        # Check rate limit
        rate_limit_tier = RATE_LIMIT_TIERS.get(
            self.auth_info.rate_limit_tier, RATE_LIMIT_TIERS["basic"]
        )
        websocket_rate_limiter.max_requests_per_minute = rate_limit_tier[
            "requests_per_minute"
        ]
        websocket_rate_limiter.max_burst = rate_limit_tier["burst"]

        if not await websocket_rate_limiter.check_rate_limit(self.client_id):
            await self._send_json(
                {
                    "error": "rate_limit_exceeded",
                    "message": "Too many requests. Please slow down.",
                    "retry_after": 60 / rate_limit_tier["requests_per_minute"],
                }
            )
            return

        msg_type = data.get("type")

        if msg_type == MessageType.SUBSCRIBE.value:
            # Add subscription pattern
            pattern = data.get("pattern", "*")
            self.subscriptions.add(pattern)
            logger.info(f"Client subscribed to pattern: {pattern}")

            # Track subscription
            await state_channel_metrics.on_subscription(self.client_id, pattern)

        elif msg_type == MessageType.UNSUBSCRIBE.value:
            # Remove subscription pattern
            pattern = data.get("pattern")
            if pattern in self.subscriptions:
                self.subscriptions.remove(pattern)
                logger.info(f"Client unsubscribed from pattern: {pattern}")

        elif msg_type == MessageType.SYNC_REQUEST.value:
            # Send full state
            await self.send_full_state()

        elif msg_type == MessageType.HEARTBEAT.value:
            # Respond to heartbeat
            await self._send_json(
                {"type": MessageType.HEARTBEAT.value, "timestamp": time.time()}
            )

        # Handle resource protocol messages
        elif data.get("method") == "resource.reserve":
            await self._send_json(
                {
                    "error": "unsupported_operation",
                    "message": "Resource reservations are no longer supported",
                }
            )

        elif data.get("method") == "resource.release":
            await self._send_json(
                {
                    "error": "unsupported_operation",
                    "message": "Resource reservations are no longer supported",
                }
            )

    def _matches_subscriptions(self, path: str) -> bool:
        """Check if path matches any subscription pattern."""
        if not self.subscriptions:
            return False

        for pattern in self.subscriptions:
            if pattern == "*":
                return True
            if pattern.endswith(".*"):
                prefix = pattern[:-2]
                if path.startswith(prefix):
                    return True
            elif path == pattern:
                return True

        return False

    async def _send_json(self, data: dict):
        """Send JSON message over WebSocket."""
        if self.websocket.client_state == WebSocketState.CONNECTED:
            await self.websocket.send_text(json.dumps(data))


@router.websocket("/ws/state")
async def state_stream(
    websocket: WebSocket,
    api_key: str | None = Query(None),
    authorization: str | None = Header(None),
    origin: str | None = Header(None),
):
    """WebSocket endpoint for real-time state streaming."""
    # Get authenticator and authenticate the connection
    authenticator = get_authenticator()
    auth_info = await authenticator.authenticate(
        api_key=api_key, auth_header=authorization, origin=origin
    )

    if not auth_info:
        logger.warning(
            f"Unauthorized WebSocket connection attempt from {websocket.client}"
        )
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    logger.info(
        f"State channel client connected from {websocket.client} "
        f"(auth: {auth_info.key_id}, tier: {auth_info.rate_limit_tier})"
    )

    async def _state_provider():
        system_resources = await resource_tracker.get_system_resources()
        models_info = resource_tracker.get_all_models_info()

        resources = {}
        for model_id, info in models_info.items():
            if hasattr(info, "status"):
                status = getattr(info.status, "value", str(info.status))
                resources[model_id] = {
                    "status": status,
                    "current_inference_start": getattr(
                        info, "current_inference_start", None
                    ),
                    "last_inference_end": getattr(info, "last_inference_end", None),
                    "load_time": getattr(info, "load_time", None),
                    "last_inference_time": getattr(info, "last_inference_time", None),
                }
            elif isinstance(info, dict):
                resources[model_id] = info
            else:
                resources[model_id] = {"status": str(info)}

        metrics = {
            "total_vram_mb": system_resources.total_vram_mb,
            "available_vram_mb": system_resources.available_vram_mb,
            "total_ram_mb": system_resources.total_ram_mb,
            "available_ram_mb": system_resources.available_ram_mb,
        }

        return {
            "gateways": {
                "local": {
                    "metrics": metrics,
                    "resources": resources,
                }
            }
        }

    # Create state channel handler with auth info
    handler = StateChannelHandler(websocket, _state_provider, auth_info)

    try:
        # Track connection AFTER registration and handshake
        logger.info(
            f"About to track connection in metrics - client_id: {handler.client_id}"
        )
        logger.info(
            f"Auth info - key_id: {auth_info.key_id}, tier: {auth_info.rate_limit_tier}"
        )
        await state_channel_metrics.on_connection(
            handler.client_id,
            {
                "auth_level": auth_info.key_id,
                "rate_limit_tier": auth_info.rate_limit_tier,
            },
        )
        logger.info("Connection tracked in metrics successfully")

        # Send initial full state
        logger.info("About to send initial full state")
        await handler.send_full_state()
        logger.info("Initial full state sent")

        # Handle incoming messages
        while True:
            data = await websocket.receive_text()
            message_size = len(data.encode("utf-8"))
            message = json.loads(data)
            await handler.handle_message(message, message_size)

    except WebSocketDisconnect:
        logger.info("State channel client disconnected")
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON received: {e}")
        await state_channel_metrics.on_error(handler.client_id, "json_decode_error")
        await websocket.close(code=1003, reason="Invalid JSON")
    except Exception as e:
        logger.error(f"State channel error: {e}")
        await state_channel_metrics.on_error(handler.client_id, type(e).__name__)
        try:
            await websocket.close(code=1011, reason="Internal error")
        except Exception:
            pass
    finally:
        # Unregister handler and track disconnection (exactly once)
        if handler:
            await state_channel_metrics.on_disconnection(handler.client_id)
            logger.info("State channel handler cleaned up")
