"""Worker lifecycle and health RPC handlers."""

from datetime import datetime

from universal_logging import format_json_for_log, get_logger

logger = get_logger(__name__)


class LifecycleHandlers:
    """Mix-in class for worker lifecycle and health RPC handlers."""

    # Assumes self.worker_id, self.model_id, self.model_config
    # Assumes self.config_received exists

    async def handle_init_config(self, params: dict) -> dict:
        """
        Handle init_config RPC request.

        Initializes worker with model configuration.

        Args:
            params: Configuration parameters including config dict

        Returns:
            Success response confirming config received
        """
        # Remove import - truncation now automatic

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

    async def handle_health(self, params: dict) -> dict:
        """
        Handle health RPC request.

        Returns:
            Health status including model_loaded state
        """
        live = bool(self.engine and self.engine.is_loaded())
        status = "ready" if live else "busy"
        models = [self.model_id] if live else []

        return {"status": status, "models": models}

    async def handle_ping(self, params: dict) -> dict:
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

    async def handle_debug_stats(self, params: dict) -> dict:
        """
        Handle debug_stats RPC request.

        Returns:
            Debug statistics from Universal Protocol
        """
        from universal_protocol.observability import get_debug_stats

        return get_debug_stats()
