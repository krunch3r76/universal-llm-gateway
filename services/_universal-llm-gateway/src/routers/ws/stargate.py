"""WebSocket endpoint for Stargate control plane."""

import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from universal_logging import get_logger

from src.core.model_registry import ModelRegistry
from src.core.websocket.connection_manager import get_connection_manager
from src.core.websocket.init_cache import InitDataCache
from src.core.websocket.messages import (
    MessageType,
    WebSocketMessage,
    create_error_message,
    create_init_message,
)

logger = get_logger(__name__)

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/stargate")
async def stargate_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for Stargate control plane.

    Protocol:
    1. On connect: Gateway sends INIT message with all startup data
    2. Gateway pushes events (MODEL_LOADED, RESOURCE_UPDATE, etc.)
    3. Keep-alive via PING/PONG
    4. Stargate can send QUERY for on-demand data (rare)

    Connection state:
    - Connected = Gateway healthy
    - Disconnected = Gateway offline (no fallback)
    """
    connection_manager = get_connection_manager()

    # Get dependencies via app.state
    app = websocket.app
    model_registry: ModelRegistry = app.state.model_registry
    worker_controller = app.state.worker_controller
    init_cache: InitDataCache = app.state.init_cache

    try:
        await connection_manager.connect(websocket)

        client_host = websocket.client.host if websocket.client else "unknown"
        logger.info(f"✅ Stargate connected from {client_host}")

        # Build and send INIT message using cached data (non-blocking)
        init_data = await init_cache.get_init_data()
        init_message = create_init_message(**init_data)

        # Log resource data for debugging
        resources = init_data.get("resources", {})
        logger.info(
            f"📊 INIT resources: VRAM={resources.get('available_vram_mb')}/{resources.get('total_vram_mb')}MB, "
            f"RAM={resources.get('available_ram_mb')}/{resources.get('total_ram_mb')}MB"
        )

        await connection_manager.send_message(websocket, init_message)
        logger.info(
            f"Sent INIT to Stargate: {len(init_message.data['models'])} models, "
            f"{len(init_message.data['loaded_models'])} loaded"
        )

        # Start keep-alive ping loop
        await connection_manager.start_ping_loop(websocket)

        # Handle incoming messages from Stargate
        while True:
            try:
                raw_message = await websocket.receive_text()
                message = json.loads(raw_message)

                msg_type = message.get("type")

                if msg_type == MessageType.PONG.value:
                    # Keep-alive response, no action needed
                    pass

                elif msg_type == MessageType.QUERY.value:
                    # Handle query (rare, for on-demand data)
                    response = await _handle_query(
                        message, model_registry, worker_controller, init_cache
                    )
                    await connection_manager.send_message(websocket, response)

                else:
                    logger.warning(f"Unknown message type from Stargate: {msg_type}")

            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON from Stargate: {e}")
                error_msg = create_error_message("invalid_json", str(e))
                await connection_manager.send_message(websocket, error_msg)

    except WebSocketDisconnect:
        # Normal disconnection - silent (expected during restarts)
        pass
    except Exception as e:
        # Unexpected error - log for diagnostics
        logger.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        await connection_manager.disconnect(websocket)


async def _handle_query(
    message: dict[str, Any],
    model_registry: ModelRegistry,
    worker_controller: Any,
    init_cache: InitDataCache,
) -> WebSocketMessage:
    """Handle QUERY message from Stargate."""
    query_type = message.get("query")
    params = message.get("params", {})
    request_id = message.get("request_id")

    try:
        if query_type == "get_model_config":
            model_id = params.get("model_id")
            config = model_registry.get_model_loader_config(model_id)
            data = {"model_id": model_id, "config": config}

        elif query_type == "get_loaded_models":
            active = (
                worker_controller.get_active_model_id() if worker_controller else None
            )
            loaded = [active] if active else []
            data = {"loaded_models": loaded}

        elif query_type == "get_resources":
            data = init_cache.get_resources()

        else:
            return create_error_message("unknown_query", f"Unknown query: {query_type}")

        return WebSocketMessage(
            type=MessageType.RESPONSE, data={"request_id": request_id, **data}
        )

    except Exception as e:
        logger.error(f"Query failed: {e}", exc_info=True)
        return create_error_message("query_failed", str(e))
