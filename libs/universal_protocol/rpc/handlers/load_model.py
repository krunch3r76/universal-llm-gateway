"""Load model RPC handler."""

import asyncio
from typing import Any

from universal_logging import get_logger

from universal_protocol.errors import RPCError

from .log_format import make_log_prefix
from .model_state import LOADED_MODELS

logger = get_logger(__name__)


async def handle_load_model(params: dict[str, Any]) -> dict[str, Any]:
    """Handle load_model RPC method.

    MVP implementation: Returns success with static context size.

    Inputs:
        params: Method parameters containing:
            - name: Model identifier (e.g., "llama-3.2")
            - path: Filesystem path to model
            - loader_config: Optional dict with max_tokens, gpu_layers, etc.
            - correlation_id: Optional correlation ID

    Outputs:
        Dict with success, model_loaded, context_size

    Raises:
        RPCError: If required parameters missing
    """
    log_prefix = make_log_prefix(params)

    name = params.get("name")
    path = params.get("path")

    if not name or not path:
        raise RPCError("INVALID_PARAMS", "name and path are required")

    # Check if model is already loaded
    if name in LOADED_MODELS:
        logger.info(f"{log_prefix} Model {name} is already loaded")
        return {
            "success": True,
            "model_loaded": True,
            "context_size": LOADED_MODELS[name]["context_size"],
        }

    # Extract loader config
    loader_config = params.get("loader_config", {})
    context_size = loader_config.get("max_tokens", 4096)

    logger.info(
        f"{log_prefix} Loading model {name} from {path} "
        f"with context_size={context_size}"
    )

    # Track loaded model
    LOADED_MODELS[name] = {
        "path": path,
        "context_size": context_size,
        "loader_config": loader_config,
        "loaded_at": asyncio.get_event_loop().time(),
    }

    return {
        "success": True,
        "model_loaded": True,
        "context_size": context_size,
    }
