"""Regular (non-streaming) inference operations."""

from typing import Any

from universal_logging import get_logger

from ..process.state import ProcessState

logger = get_logger(__name__)
structured_logger = get_logger("universal_llm_gateway.inference")


class RegularInferenceManager:
    """
    Manages regular (non-streaming) inference operations.

    Handles blocking request-response inference requests.
    """

    def __init__(self, process_state: ProcessState, gateway_config: Any):
        """
        Initialize regular inference manager.

        Args:
            process_state: ProcessState containing supervisor references
            gateway_config: Gateway configuration for timeouts
        """
        self._process_state = process_state
        self._gateway_config = gateway_config

    async def handle_regular_inference(
        self,
        model_id: str,
        messages: list[dict[str, str]] | str,
        parameters: dict[str, Any],
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Handle regular (non-streaming) inference requests.

        Args:
            model_id: Model ID to use for inference
            messages: Chat messages or prompt string
            parameters: Generation parameters

        Returns:
            Dict containing inference results

        Raises:
            RuntimeError: If supervisor not found or inference fails
        """
        # Get supervisor for this model
        supervisor = self._process_state.get_supervisor(model_id)
        if not supervisor:
            raise RuntimeError(f"No supervisor found for model {model_id}")

        # Extract timeout hint from parameters (_timeout_hint from upstream)
        timeout_hint = parameters.pop("_timeout_hint", None)

        # Get default timeout from configuration
        default_timeout = float(
            getattr(self._gateway_config.process_isolation, "worker_timeout", 1200)
        )

        # Use timeout hint if provided, otherwise use config default
        timeout = timeout_hint if timeout_hint is not None else default_timeout

        # Build RPC parameters for run_inference
        rpc_params = parameters.copy() if parameters else {}

        # Pass timeout to worker as hint for deadline enforcement
        rpc_params["timeout_hint"] = timeout

        # Preserve _request_id if present (for cancellation tracking)
        # Note: _request_id is intentionally kept (not filtered) for Worker RPC

        # Handle both string prompts and message lists
        if isinstance(messages, str):
            rpc_params["prompt"] = messages
        else:
            rpc_params["messages"] = messages

        try:
            # Use Universal Protocol RPC for non-streaming inference
            if not supervisor._http_client:
                raise RuntimeError(f"HTTP client not initialized for model {model_id}")

            logger.info(f"🚀 Starting inference for {model_id} (timeout: {timeout}s)")

            payload = await supervisor._inference_rpc_call(
                "run_inference", rpc_params, timeout=timeout
            )

            logger.info(f"✅ Inference completed for {model_id}")
        except TimeoutError:
            # Handle timeout specifically - kill the process and provide clear error
            logger.error(
                f"⏰ Inference timeout for {model_id} after {timeout}s - killing process"
            )

            # Log timeout
            structured_logger.error(
                f"{model_id}:inference_timeout: {model_id} - FAILED (error=Inference timed out after {timeout} seconds, timeout_seconds={timeout})"
            )

            # Kill the worker process that timed out
            try:
                logger.info(f"🔫 Killing timed-out worker process for {model_id}")
                await supervisor.stop(force=True, timeout=5)
                # Remove from supervisor tracking
                self._process_state.remove_supervisor(model_id)
                self._process_state.remove_socket_path(model_id)
                logger.info(
                    f"✅ Successfully killed timed-out worker process for {model_id}"
                )
            except Exception as kill_error:
                logger.error(
                    f"❌ Failed to kill timed-out worker process for {model_id}: {kill_error}"
                )

            raise RuntimeError(
                f"Inference timed out after {timeout} seconds - worker process terminated"
            )

        # Extract from process_ipc envelope using schema utility
        result_data = payload

        # Check for domain-level errors
        if "error" in result_data:
            raise RuntimeError(f"Worker error: {result_data['error']}")

        # Log successful inference
        structured_logger.info(
            f"{model_id}:inference_completed: {model_id} - SUCCESS (finish_reason={result_data.get('finish_reason', 'unknown')})"
        )

        return result_data
