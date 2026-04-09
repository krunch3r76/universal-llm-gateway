"""Inference execution RPC handlers."""

import asyncio
import time
import uuid

from universal_event_bus.events.debug import emit_debug_event
from universal_logging import get_logger
from universal_protocol.errors import EngineError

from ..deadline import enforce_deadline

logger = get_logger(__name__)


async def _emit_worker_inference_debug(
    *,
    step: str,
    worker_id: str,
    model_id: str,
    request_id: str,
    **extra: object,
) -> None:
    """Emit debug-only worker inference phase markers."""
    await emit_debug_event(
        "debug.worker.inference",
        {
            "step": step,
            "worker_id": worker_id,
            "model_id": model_id,
            "request_id": request_id,
            **extra,
        },
        source="worker",
    )


class InferenceHandlers:
    """Mix-in class for inference execution RPC handlers."""

    # Assumes self.engine, self.model_loaded, self._inference_gate
    # Assumes self._build_non_streaming_engine_request() exists

    async def handle_run_inference(self, params: dict) -> dict:
        """
        Handle run_inference RPC request (non-streaming) with gate and cancellation.

        INVARIANT: Pure passthrough - parameters flow unchanged to engine.

        Waits for inference slot via FIFO gate, then executes inference.

        Args:
            params: Inference parameters (pure passthrough from Gateway)

        Returns:
            Inference result from engine
        """
        from ..stream_lifecycle import cleanup_stream_entry, register_stream_entry

        request_id = params.get("_request_id", str(uuid.uuid4()))
        timeout_hint = params.get("timeout_hint")

        if not self.engine or not self.engine.is_loaded():
            raise EngineError(code="MODEL_NOT_LOADED", message="Model not loaded")

        gate = self._inference_gate
        if not gate:
            raise EngineError(
                code="INTERNAL_ERROR",
                message="Inference gate not initialized",
            )

        # Register with "request" kind (creates cancellation_event)
        cancellation_event = register_stream_entry(request_id, kind="request")

        logger.info(
            f"🔧 [worker] Running non-streaming inference (request_id={request_id})"
        )

        gate_acquired = False
        gate_wait_start = time.monotonic()
        worker_id = getattr(self, "worker_id", "unknown")
        model_id = getattr(self, "model_id", "unknown")
        try:
            await _emit_worker_inference_debug(
                step="gate_wait_start",
                worker_id=worker_id,
                model_id=model_id,
                request_id=request_id,
                timeout_hint=timeout_hint,
            )
            # Wrap inference with deadline enforcement
            async with enforce_deadline(timeout_hint, cancellation_event, request_id):
                await gate.acquire(
                    request_id,
                    timeout=timeout_hint,
                    cancellation_event=cancellation_event,
                )
                gate_acquired = True
                queue_wait_ms = (time.monotonic() - gate_wait_start) * 1000.0

                from ..events import emit_inference_dequeued

                await emit_inference_dequeued(
                    worker_id=worker_id,
                    model_id=model_id,
                    request_id=request_id,
                    queue_wait_ms=queue_wait_ms,
                )
                await _emit_worker_inference_debug(
                    step="gate_acquired",
                    worker_id=worker_id,
                    model_id=model_id,
                    request_id=request_id,
                    queue_wait_ms=round(queue_wait_ms, 1),
                )

                if cancellation_event.is_set():
                    await _emit_worker_inference_debug(
                        step="cancel_before_generate",
                        worker_id=worker_id,
                        model_id=model_id,
                        request_id=request_id,
                    )
                    raise EngineError(
                        code="CANCELLED", message="Request cancelled before start"
                    )

                data = self._build_non_streaming_engine_request(params)
                generate_start = time.monotonic()
                await _emit_worker_inference_debug(
                    step="generate_start",
                    worker_id=worker_id,
                    model_id=model_id,
                    request_id=request_id,
                    payload_keys=sorted(data.keys()),
                )
                result = await self.engine.generate(data, cancellation_event)
                await _emit_worker_inference_debug(
                    step="generate_done",
                    worker_id=worker_id,
                    model_id=model_id,
                    request_id=request_id,
                    elapsed_ms=round((time.monotonic() - generate_start) * 1000.0, 1),
                    finish_reason=(
                        result.get("finish_reason")
                        if isinstance(result, dict)
                        else None
                    ),
                )

                logger.info("✅ [worker] Non-streaming inference completed")
                return result

        except TimeoutError:
            await _emit_worker_inference_debug(
                step="timeout",
                worker_id=worker_id,
                model_id=model_id,
                request_id=request_id,
                gate_acquired=gate_acquired,
                timeout_hint=timeout_hint,
            )
            logger.error(
                f"❌ [worker] [{request_id}] Queue timeout after {timeout_hint}s"
            )
            raise EngineError(
                code="QUEUE_TIMEOUT",
                message=f"Timeout waiting for inference slot ({timeout_hint}s)",
            )
        except asyncio.CancelledError:
            await _emit_worker_inference_debug(
                step="cancelled",
                worker_id=worker_id,
                model_id=model_id,
                request_id=request_id,
                gate_acquired=gate_acquired,
            )
            logger.info(f"🛑 [worker] [{request_id}] Cancelled while queued")
            raise EngineError(
                code="CANCELLED", message="Request cancelled while queued"
            )
        except EngineError as exc:
            await _emit_worker_inference_debug(
                step="engine_error",
                worker_id=worker_id,
                model_id=model_id,
                request_id=request_id,
                gate_acquired=gate_acquired,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise
        except Exception as e:
            if cancellation_event.is_set():
                await _emit_worker_inference_debug(
                    step="cancelled_during_generate",
                    worker_id=worker_id,
                    model_id=model_id,
                    request_id=request_id,
                )
                logger.info(f"🛑 [worker] Inference cancelled for request {request_id}")
                raise EngineError(code="CANCELLED", message="Inference cancelled")
            await _emit_worker_inference_debug(
                step="unexpected_error",
                worker_id=worker_id,
                model_id=model_id,
                request_id=request_id,
                gate_acquired=gate_acquired,
                error_type=type(e).__name__,
                error=str(e),
            )
            logger.error(f"❌ [worker] Non-streaming inference failed: {e}")
            raise self._map_exception_to_engine_error(e)

        finally:
            # CRITICAL: Release gate if acquired (prevents gate slot leaks)
            if gate_acquired:
                await _emit_worker_inference_debug(
                    step="gate_release_start",
                    worker_id=worker_id,
                    model_id=model_id,
                    request_id=request_id,
                )
                await asyncio.shield(gate.release())
                await _emit_worker_inference_debug(
                    step="gate_released",
                    worker_id=worker_id,
                    model_id=model_id,
                    request_id=request_id,
                )
            # Cleanup (centralized)
            await cleanup_stream_entry(request_id, reason="request_complete")
