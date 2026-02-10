"""Streaming inference start/registration handlers (worker-side)."""

import asyncio
from typing import Any

from universal_logging import get_logger
from universal_protocol.errors import EngineError
from universal_protocol.ids import generate_stream_id
from universal_protocol.ws.lifecycle import StreamContext
from universal_protocol.ws.registry import stream_registry
from universal_protocol.ws.stream_queue import UnboundedStreamQueue

from ..stream_lifecycle import register_stream_entry

logger = get_logger(__name__)


class StreamInferenceStartHandlers:
    """Mix-in class providing streaming start + validation methods.

    Assumes: self.engine, self.model_config, self.model_loaded, self._inference_gate.
    """

    def _validate_stream_params(
        self, params: dict[str, Any]
    ) -> tuple[str | None, list[dict[str, Any]] | None, int, float | None]:
        """Validate streaming inference parameters.

        Returns:
            (prompt, messages, max_tokens, temperature)

        Raises:
            EngineError: On validation failure
        """
        prompt = params.get("prompt")
        messages = params.get("messages")
        max_tokens = params.get("max_tokens")
        temperature = params.get("temperature")

        # Validate prompt/messages: exactly one must be provided
        if prompt and messages:
            raise EngineError(
                code="INVALID_PARAMS", message="Cannot provide both prompt and messages"
            )

        if not prompt and not messages:
            raise EngineError(
                code="INVALID_PARAMS", message="Either prompt or messages is required"
            )

        # Validate messages format if provided
        if messages:
            if not isinstance(messages, list) or len(messages) == 0:
                raise EngineError(
                    code="INVALID_PARAMS", message="messages must be a non-empty array"
                )
            # Basic validation that each message has role and content
            for msg in messages:
                if (
                    not isinstance(msg, dict)
                    or "role" not in msg
                    or "content" not in msg
                ):
                    raise EngineError(
                        code="INVALID_PARAMS",
                        message="Each message must have 'role' and 'content' fields",
                    )

        # Validate max_tokens
        if max_tokens is None:
            raise EngineError(code="INVALID_PARAMS", message="max_tokens is required")

        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise EngineError(
                code="INVALID_PARAMS", message="max_tokens must be a positive integer"
            )

        # Validate temperature if provided
        if temperature is not None:
            if not isinstance(temperature, int | float) or not (
                0.0 <= temperature <= 2
            ):
                raise EngineError(
                    code="INVALID_PARAMS",
                    message="temperature must be between 0.0 and 2.0",
                )

        return prompt, messages, max_tokens, temperature

    async def handle_start_inference(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle start_inference RPC request.

        Registers stream synchronously, launches background task.
        Queue slot is acquired in the background task (stream_inference),
        allowing the request to return immediately with stream_id.
        """
        request_id = params.get("_request_id", "unknown")

        if not self.model_loaded or not self.engine:
            raise EngineError(code="MODEL_NOT_LOADED", message="Model not loaded")

        if not self._inference_gate:
            raise EngineError(
                code="INTERNAL_ERROR",
                message="Inference gate not initialized",
            )

        engine_type = getattr(self.engine, "engine_type", "unknown")

        # 1. Validate parameters
        prompt, messages, _max_tokens, _temperature = self._validate_stream_params(
            params
        )

        # 2. Log gate state (no rejection - gate handles serialization)
        stats = self._inference_gate.stats
        logger.info(
            f"🔍 [worker] [request_id={request_id}] Engine type: {engine_type}, "
            f"active={stats.active}/{stats.limit}, "
            f"registry_count: {len(stream_registry)}, "
            f"queued={stats.queued}"
        )

        # 3. Atomic registration: register + create task + assign
        # CRITICAL: No await between steps ensures cancel requests see task
        stream_id = generate_stream_id()

        logger.info(
            f"🔧 [worker] [request_id={request_id}] "
            f"Starting inference stream {stream_id}"
        )

        # 3a. Pre-register stream with task=None (creates cancellation_event)
        context = StreamContext(stream_id)
        queue = UnboundedStreamQueue(stream_id)
        cancellation_event = register_stream_entry(
            stream_id,
            kind="stream",
            context=context,
            queue=queue,
            task=None,
        )

        logger.info(
            f"✅ [worker] [request_id={request_id}] Pre-registered {stream_id}. "
            f"Total active: {len(stream_registry)}"
        )

        # 3b. Create task immediately (no await yet - atomic with registration)
        task = asyncio.create_task(
            self.stream_inference(
                stream_id=stream_id,
                context=context,
                queue=queue,
                cancellation_event=cancellation_event,
                prompt=prompt,
                messages=messages,
                parameters={
                    k: v
                    for k, v in params.items()
                    if k
                    not in (
                        "prompt",
                        "messages",
                        "worker_id",
                        "correlation_id",
                        "_request_id",
                    )
                },
                request_id=request_id,
            )
        )
        context.add_task(task)

        # 3c. Assign task to entry (still atomic - no await yet)
        entry = stream_registry.get(stream_id)
        if not entry:
            # Should never happen - entry just registered
            task.cancel()  # Clean up orphaned task
            raise EngineError(
                code="INTERNAL_ERROR",
                message=(
                    f"Stream {stream_id} disappeared from registry "
                    f"(race condition or bug)"
                ),
            )
        entry.task = task

        logger.info(
            f"✅ [worker] [request_id={request_id}] "
            f"Stream {stream_id} started with task"
        )
        return {"stream_id": stream_id, "websocket_path": f"/stream/{stream_id}"}
