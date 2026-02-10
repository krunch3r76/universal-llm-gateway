"""Model metadata and utility RPC handlers."""

from datetime import datetime

from universal_logging import get_logger
from universal_protocol.errors import EngineError

logger = get_logger(__name__)


class MetadataHandlers:
    """Mix-in class for metadata and utility RPC handlers."""

    # Assumes self.model_id, self.engine, self.model_loaded
    # Assumes self._handle_count_tokens() exists

    async def handle_list_models(self, params: dict) -> dict:
        """
        Handle list_models RPC request.

        Returns:
            List of loaded models with their info
        """
        loaded_models = []
        if self.model_loaded and self.engine:
            try:
                model_info = self.engine.get_model_info()  # Synchronous method
                loaded_models.append(model_info)
            except Exception as e:
                logger.error(
                    f"❌ [worker] Error getting model info for list_models: {e}"
                )
                return {"success": False, "message": f"Error listing models: {e}"}
        return {"success": True, "models": loaded_models}

    async def handle_get_model_info(self, params: dict) -> dict:
        """
        Handle get_model_info RPC request.

        Returns:
            Model information from engine

        Raises:
            EngineError: If model not loaded
        """
        if not self.model_loaded or not self.engine:
            raise EngineError(
                code="MODEL_NOT_LOADED", message="Model engine not loaded"
            )

        try:
            model_info = self.engine.get_model_info()  # Synchronous method

            return {"model_info": model_info, "timestamp": datetime.now().isoformat()}
        except Exception as e:
            logger.error(f"❌ [worker] Error getting model info: {e}")
            raise self._map_exception_to_engine_error(e)

    async def handle_count_tokens(self, params: dict) -> dict:
        """
        Handle count_tokens RPC request.

        Accepts:
        - text: str (spec-defined format)
        - prompt: str (backward compatibility)
        - messages: list (backward compatibility)

        Args:
            params: Token counting parameters (text/prompt/messages)

        Returns:
            Token count result
        """
        # Accept spec-defined "text" field OR legacy "prompt"/"messages"
        text = params.get("text")
        prompt = params.get("prompt")
        messages = params.get("messages")

        # Normalize: if "text" provided, convert to "prompt" format
        if text:
            if not isinstance(text, str):
                raise EngineError(
                    code="INVALID_PARAMS", message="text must be a string"
                )
            prompt = text  # Normalize to prompt format
        elif not prompt and not messages:
            raise EngineError(
                code="INVALID_PARAMS",
                message="Either 'text', 'prompt', or 'messages' is required",
            )

        # Convert to format expected by existing method
        command = {
            "use_cpu": params.get("use_cpu", False),
            "context_length": params.get("context_length"),
        }

        # Add either prompt or messages
        if messages:
            command["messages"] = messages
        else:
            command["prompt"] = prompt

        result = await self._handle_count_tokens(command)

        # Return the full result for process_command compatibility
        return result
