"""Shared helper functions for RPC handlers."""

from universal_logging import get_logger
from universal_protocol.errors import EngineError

logger = get_logger(__name__)


class RPCHelpers:
    """Mix-in class for shared RPC helper functions."""

    def _build_non_streaming_engine_request(self, params: dict) -> dict:
        """
        Build engine request payload for non-streaming inference.

        INVARIANT: Pure passthrough - removes only routing metadata.

        Pre: ("prompt" ∈ params) ∨ ("messages" ∈ params)
        Post: "stream" = False ∧ routing metadata removed

        Routing metadata removed: worker_id, correlation_id, _request_id,
        timeout_hint. All generation parameters pass through unchanged to
        engine.
        """
        data = params.copy()
        for key in ["worker_id", "correlation_id", "_request_id", "timeout_hint"]:
            data.pop(key, None)
        data["stream"] = False

        prompt = params.get("prompt")
        messages = params.get("messages")
        if not prompt and not messages:
            raise EngineError(
                code="INVALID_PARAMS",
                message="Either 'prompt' or 'messages' must be provided",
            )
        return data

    def _map_exception_to_engine_error(self, e: Exception) -> EngineError:
        """Map common exceptions to appropriate EngineError codes.

        Args:
            e: The exception to map

        Returns:
            EngineError with appropriate code and message
        """
        error_str = str(e)
        error_type = type(e).__name__

        # OOM errors
        if (
            isinstance(e, MemoryError)
            or "out of memory" in error_str.lower()
            or "oom" in error_str.lower()
        ):
            return EngineError(
                code="OOM",
                message=f"Out of memory: {error_str}",
                data={"error_type": error_type},
            )

        # Model file errors
        if (
            isinstance(e, FileNotFoundError)
            or "not found" in error_str.lower()
            or "does not exist" in error_str.lower()
        ):
            return EngineError(
                code="MODEL_NOT_FOUND",
                message=f"Model file not found: {error_str}",
                data={"error_type": error_type},
            )

        # CUDA errors
        if "cuda" in error_str.lower() or "gpu" in error_str.lower():
            return EngineError(
                code="CUDA_ERROR",
                message=f"CUDA/GPU error: {error_str}",
                data={"error_type": error_type},
            )

        # Timeout errors
        import asyncio

        if isinstance(e, asyncio.TimeoutError) or "timeout" in error_str.lower():
            return EngineError(
                code="TIMEOUT",
                message=f"Operation timed out: {error_str}",
                data={"error_type": error_type},
            )

        # Generic runtime errors from engine
        if isinstance(e, RuntimeError):
            return EngineError(
                code="ENGINE_ERROR",
                message=f"Engine runtime error: {error_str}",
                data={"error_type": error_type},
            )

        # Generic engine error for everything else
        return EngineError(
            code="ENGINE_ERROR",
            message=f"Engine error: {error_str}",
            data={"error_type": error_type},
        )
