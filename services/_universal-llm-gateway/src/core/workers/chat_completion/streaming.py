"""Streaming chat completion operations."""

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from src.core.errors import is_connection_error

if TYPE_CHECKING:
    from ..controller import WorkerController


def _get_resource_tracker():
    """Lazy import to avoid circular dependency."""
    from src.core.resources import resource_tracker

    return resource_tracker


logger = get_logger(__name__)


class StreamingChatCompletion:
    """
    Handles streaming chat completion generation.

    Extracted from WorkerController to reduce file size.
    """

    def __init__(self, controller: "WorkerController"):
        self._controller = controller

    async def inference_stream(
        self,
        model_id: str,
        messages: list[dict[str, str]] | str,
        parameters: dict[str, Any],
        correlation_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Handle streaming inference as async generator.

        Yields chunks immediately as they arrive from worker.
        """
        async for chunk in self._controller._streaming_inference.inference_stream(
            model_id, messages, parameters, correlation_id
        ):
            yield chunk

    async def generate_chat_completion_stream(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        correlation_id: str | None = None,
        **kwargs,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Generate streaming chat completion with resource tracking.
        """
        model_marked_busy = False

        try:
            if not await self._controller._model_loader.ensure_model_loaded(model_id):
                raise RuntimeError(f"Failed to load model {model_id}")

            kwargs["stream"] = True

            # INVARIANT: Pure passthrough - parameters flow unchanged to worker/engine
            # Gateway does not validate, transform, or add defaults
            # (Stargate's responsibility)
            # Log generation parameters being sent to worker
            from universal_logging import format_json_for_log

            logger.info(
                f"🎛️  WORKER (streaming): Generation parameters for {model_id}: "
                f"{format_json_for_log(kwargs)}"
            )

            logger.info(f"🔧 [controller] Starting streaming inference for {model_id}")

            resource_tracker = _get_resource_tracker()

            await resource_tracker.set_model_busy(model_id)
            model_marked_busy = True
            resource_tracker.set_model_inference_state(model_id, "token_counting")

            try:
                async for chunk in self._stream_with_tracking(
                    model_id, messages, kwargs, correlation_id, resource_tracker
                ):
                    model_marked_busy = yield chunk
                    if model_marked_busy is None:
                        model_marked_busy = True

            except asyncio.CancelledError:
                try:
                    # Shield cancellation handler from being cancelled
                    # Ensures cleanup completes even if client disconnects
                    await asyncio.shield(
                        self._handle_cancellation(model_id, model_marked_busy)
                    )
                except Exception as cancel_error:
                    logger.error(
                        f"❌ [controller] Failed to handle cancellation "
                        f"for {model_id}: {cancel_error}",
                        exc_info=True,
                    )
                raise

            except Exception as stream_error:
                await self._handle_stream_error(
                    model_id,
                    stream_error,
                    model_marked_busy,
                )
                raise stream_error

        except Exception as e:
            if model_marked_busy:
                await self._cleanup_busy_state(model_id)

            error_message = str(e)
            lower_error = error_message.lower()
            if "cancel" in lower_error or "disconnect" in lower_error:
                logger.warning(
                    "🔍 [controller] Potential missed cancellation: "
                    f"{type(e).__name__}: {error_message}"
                )

            logger.error(
                "❌ [controller] Streaming error for "
                f"{model_id}: {type(e).__name__}: {error_message}",
                exc_info=True,
            )

            self._handle_transport_error(error_message, model_id, "streaming")
            raise

    async def _stream_with_tracking(
        self,
        model_id: str,
        messages: list,
        kwargs: dict,
        correlation_id: str | None,
        resource_tracker,
    ) -> AsyncIterator[tuple[dict, bool]]:
        """Stream with state tracking, yielding (chunk, model_marked_busy)."""
        logger.info(f"🔍 [controller] Starting inference_stream for {model_id}")
        first_chunk_received = False
        model_marked_ready = False
        model_marked_busy = True

        async for chunk in self._controller._streaming_inference.inference_stream(
            model_id, messages, kwargs, correlation_id
        ):
            if not first_chunk_received:
                resource_tracker.set_model_inference_state(model_id, "generating")
                first_chunk_received = True

            finish_reason = self._get_finish_reason(chunk)

            if not model_marked_ready and finish_reason == "stop":
                logger.info(
                    f"✅ [controller] Stream completion for {model_id} - marking idle"
                )
                await resource_tracker.set_model_idle(model_id)
                model_marked_ready = True
                model_marked_busy = False

            yield chunk

        if not model_marked_ready and model_marked_busy:
            model_marked_busy = await self._finalize_stream(model_id)

    def _get_finish_reason(self, chunk: dict) -> str | None:
        """Extract finish_reason from chunk."""
        finish_reason = chunk.get("finish_reason")
        if (
            not finish_reason
            and isinstance(chunk, dict)
            and "choices" in chunk
            and chunk["choices"]
        ):
            choice = chunk["choices"][0]
            if isinstance(choice, dict):
                finish_reason = choice.get("finish_reason")
        return finish_reason

    async def _finalize_stream(self, model_id: str) -> bool:
        """Finalize stream and verify worker health."""
        resource_tracker = _get_resource_tracker()

        try:
            worker_alive = await self._controller.is_model_loaded(model_id)
            if worker_alive:
                logger.info(f"🔍 [controller] Stream completed normally for {model_id}")
                await resource_tracker.set_model_idle(model_id)
                return False
            logger.error(
                "🚨 [controller] Stream exited but worker %s not responsive", model_id
            )
            resource_tracker.set_model_error(
                model_id, "Worker became unresponsive during streaming"
            )
            return False
        except Exception as e:
            logger.error(f"❌ [controller] Failed to check worker for {model_id}: {e}")
            resource_tracker.set_model_error(
                model_id, f"Worker health check failed: {e}"
            )
            return False

    async def _handle_cancellation(self, model_id: str, model_marked_busy: bool):
        """Handle stream cancellation from client disconnect."""
        from ..cancellation import emit_stream_cancelled_or_force_idle

        logger.info(f"🔌 Stream cancelled for {model_id} - emitting cancellation event")

        reason = "client_disconnect" if model_marked_busy else "client_disconnect_early"

        if model_marked_busy:
            await emit_stream_cancelled_or_force_idle(
                model_id,
                stream_id=None,
                reason=reason,
                event_bus=self._controller.event_bus,
            )
        else:
            logger.debug(
                "🔍 [controller] Model %s not marked busy, emitting event anyway",
                model_id,
            )
            try:
                await emit_stream_cancelled_or_force_idle(
                    model_id,
                    stream_id=None,
                    reason=reason,
                    event_bus=self._controller.event_bus,
                )
            except Exception as e:
                logger.warning(
                    "⚠️ [controller] Failed to emit cancellation for %s: %s",
                    model_id,
                    e,
                )

    async def _handle_stream_error(
        self, model_id: str, error: Exception, model_marked_busy: bool
    ):
        """Handle stream errors."""
        logger.error(f"❌ [controller] Stream error for {model_id}: {error}")
        resource_tracker = _get_resource_tracker()

        if model_marked_busy:
            try:
                worker_alive = await self._controller.is_model_loaded(model_id)
                if worker_alive:
                    await resource_tracker.set_model_idle(model_id)
                else:
                    logger.warning(
                        f"⚠️ [controller] Worker {model_id} not responsive after error"
                    )
            except Exception as e:
                logger.warning(
                    f"⚠️ [controller] Failed to check worker for {model_id}: {e}"
                )

    async def _cleanup_busy_state(self, model_id: str):
        """Cleanup busy state on exception."""
        logger.info(f"🔧 [controller] Cleaning up busy state for {model_id}")
        try:
            await _get_resource_tracker().set_model_idle(model_id)
        except Exception as cleanup_error:
            logger.error(f"❌ [controller] Failed to cleanup state: {cleanup_error}")

    def _handle_transport_error(self, error_message: str, model_id: str, context: str):
        """Handle transport/connection errors."""
        if is_connection_error(error_message):
            logger.error(
                "🚨 [controller] Transport error during %s for %s: %s",
                context,
                model_id,
                error_message,
            )
            raise RuntimeError(f"Worker connection failed: {error_message}")
        if "timed out" in error_message.lower():
            logger.error(
                "⏰ [controller] Timeout during %s for %s: %s",
                context,
                model_id,
                error_message,
            )
            raise RuntimeError(f"Operation timed out: {error_message}")
