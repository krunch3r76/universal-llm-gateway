"""Worker lifecycle and health RPC handlers."""

from datetime import datetime
from typing import Any

from universal_logging import format_json_for_log, get_logger

logger = get_logger(__name__)


class LifecycleHandlers:
    """Mix-in class for worker lifecycle and health RPC handlers.

    Expects on the instance: worker_id, model_id, model_config, config_received,
    engine, and optionally _inference_gate.
    """

    async def handle_init_config(self, params: dict) -> dict:
        """
        Handle init_config RPC request.

        Initializes worker with model configuration.

        Args:
            params: Configuration parameters including config dict

        Returns:
            Success response confirming config received
        """
        config = params.get("config", {})

        logger.info(
            f"🔧 [worker] Initializing config: {format_json_for_log(config)}"  # Unicode + automatic truncation
        )

        self.model_config = config
        self.config_received = True

        return {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "config_received": True,
            "ready_for_model_loading": True,
            "message": "Config received. Use 'load_model' command to load the model.",
        }

    async def handle_health(self, params: dict[str, Any]) -> dict:
        """
        Handle health RPC request.

        Returns model_loaded state, engine_pid, and inference gate activity.
        The gate stats (inference_active, inference_limit) are the source of
        truth for whether the worker is actually processing inference — do NOT
        rely on "status" alone (it only reflects model-in-memory).
        """
        model_loaded = bool(self.engine and self.engine.is_loaded())
        models = [self.model_id] if model_loaded and self.model_id else []
        engine_pid = self.engine.get_engine_pid() if self.engine else None

        gate = getattr(self, "_inference_gate", None)
        inference_active = 0
        inference_limit = 0
        if gate is not None:
            stats = gate.stats
            inference_active = stats.active
            inference_limit = stats.limit

        status = (
            "not_loaded"
            if not model_loaded
            else "inferring"
            if inference_active > 0
            else "idle"
        )

        result: dict = {
            "status": status,
            "model_loaded": model_loaded,
            "models": models,
            "inference_active": inference_active,
            "inference_limit": inference_limit,
        }
        if engine_pid is not None:
            result["engine_pid"] = engine_pid
        return result

    async def handle_ping(self, params: dict[str, Any]) -> dict:
        """
        Handle ping RPC request (connectivity test).

        Returns:
            Pong response
        """
        return {
            "status": "pong",
            "timestamp": datetime.now().isoformat(),
            "worker_id": self.worker_id,
            "model_loaded": bool(self.engine and self.engine.is_loaded()),
        }

    async def handle_debug_stats(self, params: dict[str, Any]) -> dict:
        """
        Handle debug_stats RPC request.

        Returns:
            Debug statistics from Universal Protocol
        """
        from universal_protocol.observability import get_debug_stats

        return get_debug_stats()
