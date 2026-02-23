"""Non-streaming chat completion operations."""

import time
import uuid
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from src.core.errors import is_connection_error

if TYPE_CHECKING:
    from ..controller import WorkerController


def _get_resource_tracker():
    from src.core.resources import resource_tracker

    return resource_tracker


logger = get_logger(__name__)
structured_logger = get_logger("universal_llm_gateway.chat_completion")


class NonStreamingChatCompletion:
    """Handles non-streaming chat completion. Extracted from WorkerController."""

    def __init__(self, controller: "WorkerController"):
        self._controller = controller

    async def inference(
        self,
        model_id: str,
        messages: list | str,
        parameters: dict,
        correlation_id: str | None = None,
    ) -> dict:
        """Send non-streaming inference request to worker."""
        if parameters.get("stream", False):
            raise ValueError(
                "inference() does not support streaming. "
                "Use generate_chat_completion_stream()."
            )
        try:
            msg_count = len(messages) if isinstance(messages, list) else 1
            structured_logger.info(
                "%s:inference_request: SUCCESS (messages=%s)", model_id, msg_count
            )
            return await self._controller._regular_inference.handle_regular_inference(
                model_id, messages, parameters, correlation_id
            )
        except Exception as e:
            self._log_inference_error(model_id, e)
            raise

    def _log_inference_error(self, model_id: str, e: Exception):
        """Log inference errors with categorization."""
        msg = str(e).lower()
        if "timed out" in msg:
            logger.error(f"⏰ Inference timeout for {model_id}: {e}")
        elif "memory" in msg or "oom" in msg:
            logger.error(f"💾 Memory error for {model_id}: {e}")
        elif "connection" in msg or "socket" in msg:
            logger.error(f"🔌 Connection error for {model_id}: {e}")
        else:
            logger.error(f"❌ Inference failed for {model_id}: {e}")
        structured_logger.error(f"{model_id}:inference_failed: FAILED (error={e})")

    async def generate_chat_completion(
        self,
        model_id: str,
        messages: list | str,
        correlation_id: str | None = None,
        **kwargs,
    ) -> dict:
        """Generate chat completion with resource tracking."""
        request_id = str(uuid.uuid4())

        worker_config = (
            self._get_worker_config(model_id)
            if self._controller.resource_monitor_enabled
            else {}
        )

        try:
            # Extract _request_id before filtering (preserve for Worker RPC)
            parameters = {k: v for k, v in kwargs.items() if v is not None}

            # INVARIANT: Pure passthrough - parameters flow unchanged to worker/engine
            # Gateway does not validate, transform, or add defaults
            # (Stargate's responsibility)
            # Log generation parameters being sent to worker
            from universal_logging import format_json_for_log

            logger.info(
                f"🎛️  WORKER: Generation parameters for {model_id}: "
                f"{format_json_for_log(parameters)}"
            )

            logger.info(f"🔍 CONTROLLER: Sending inference to {model_id}")
            if self._controller.resource_monitor_enabled:
                try:
                    self._controller.reset_peak_usage(model_id)
                except Exception:
                    pass

            resource_tracker = _get_resource_tracker()

            async with resource_tracker.track_inference(model_id):
                resource_tracker.set_model_inference_state(model_id, "token_counting")
                try:
                    resource_tracker.set_model_inference_state(model_id, "generating")
                    response = await self.inference(
                        model_id, messages, parameters, correlation_id
                    )
                except Exception as e:
                    self._handle_transport_error(str(e), model_id, "inference")
                    raise

            return self._format_response(response, model_id)
        except Exception as e:
            logger.error(
                f"❌ Generation failed for {model_id}: {type(e).__name__}: {e}",
                exc_info=True,
            )
            raise
        finally:
            await self._capture_peak_usage(model_id, request_id, worker_config)

    def _get_worker_config(self, model_id: str) -> dict:
        """Get worker configuration for resource monitoring."""
        try:
            info = self._controller.model_registry.get_model_info(model_id)
            if info:
                return {
                    "model_id": model_id,
                    "model_name": info.name,
                    "model_format": info.format,
                }
        except Exception:
            pass
        return {}

    def _format_response(self, response: dict, model_id: str) -> dict:
        """Format worker response for API layer."""
        content, finish_reason = "", "stop"
        tool_calls = None
        if "choices" in response and response.get("choices"):
            c = response["choices"][0]
            if "message" in c:
                msg = c["message"]
                content = msg.get("content", "")
                finish_reason = c.get("finish_reason", "stop")
                tool_calls = msg.get("tool_calls")
            elif "text" in c:
                content = c.get("text", "")
                finish_reason = c.get("finish_reason", "stop")
        usage = response.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
        result: dict = {
            "content": content,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "finish_reason": finish_reason,
            "model_id": model_id,
        }
        if tool_calls is not None:
            result["tool_calls"] = tool_calls
        # Preserve llama.cpp timings for inference duration observability.
        # timings.predicted_ms = actual generation time (excludes queue wait).
        if "timings" in response:
            result["timings"] = response["timings"]
        return result

    async def _capture_peak_usage(
        self, model_id: str, request_id: str, worker_config: dict
    ):
        """Capture and publish peak resource usage."""
        if not self._controller.resource_monitor_enabled:
            return
        try:
            peak = self._controller.get_peak_usage(model_id)
            if peak and self._controller.event_bus:
                from src.core.events.types import InferenceResourceUpdate

                await self._controller.event_bus.publish_async_nowait(
                    InferenceResourceUpdate(
                        model_id=model_id,
                        request_id=request_id,
                        timestamp=time.time(),
                        peak_ram_gb=peak.get("peak_ram_gb", 0),
                        peak_vram_gb=peak.get("peak_vram_gb", 0),
                        worker_config=worker_config,
                    )
                )
        except Exception:
            pass

    def _handle_transport_error(self, error_message: str, model_id: str, context: str):
        """Handle transport/connection errors."""
        if is_connection_error(error_message):
            raise RuntimeError(f"Worker connection failed: {error_message}")
        elif "timed out" in error_message.lower():
            raise RuntimeError(f"Operation timed out: {error_message}")

    async def count_tokens(
        self,
        model_id: str,
        message_or_prompt: list | str,
        use_cpu: bool,
        context_length: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Count tokens using worker process."""
        try:
            logger.info(f"🔍 Token counting for {model_id} (use_cpu: {use_cpu})")
            command: dict[str, Any] = {
                "command_type": "count_tokens",
                "context_length": context_length,
                "use_cpu": use_cpu,
            }
            if isinstance(message_or_prompt, list):
                command["messages"] = message_or_prompt
            else:
                command["prompt"] = message_or_prompt
            if tools:
                command["tools"] = tools

            supervisor = self._controller._process_state.get_supervisor(model_id)
            if not supervisor:
                raise RuntimeError(f"No supervisor for {model_id}")

            timeout = float(
                getattr(
                    self._controller.gateway_config.process_isolation,
                    "token_counting_timeout",
                    60,
                )
            )
            payload = await supervisor.execute_command(command, timeout=timeout)
            if "error" in payload:
                raise RuntimeError(f"Worker error: {payload['error']}")
            return self._parse_token_count_result(payload, model_id)
        except Exception as e:
            logger.error(f"❌ Token counting failed: {e}")
            raise

    def _parse_token_count_result(self, payload: dict, model_id: str) -> dict:
        """Parse token counting result from worker."""
        if "count" in payload:
            extra = payload.get("data", {})
            return {
                "token_count": payload.get("count", 0),
                "method_used": payload.get("method", "exact"),
                "confidence": extra.get("confidence", 1.0),
                "model_id": extra.get("model_id", model_id),
            }
        return {
            "token_count": payload.get("token_count", 0),
            "method_used": "exact_tokenization"
            if payload.get("method_used") == "exact_tokenization"
            else "unknown",
            "confidence": payload.get("confidence", 0.0),
            "model_id": payload.get("model_id", model_id),
        }
