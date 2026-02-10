"""Inference execution RPC handlers."""

import asyncio
import uuid

from universal_logging import get_logger
from universal_protocol.errors import EngineError

from ..deadline import enforce_deadline

logger = get_logger(__name__)


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

        if not self.model_loaded or not self.engine:
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
        try:
            # Wrap inference with deadline enforcement
            async with enforce_deadline(timeout_hint, cancellation_event, request_id):
                await gate.acquire(
                    request_id,
                    timeout=timeout_hint,
                    cancellation_event=cancellation_event,
                )
                gate_acquired = True

                if cancellation_event.is_set():
                    raise EngineError(
                        code="CANCELLED", message="Request cancelled before start"
                    )

                data = self._build_non_streaming_engine_request(params)
                result = await self.engine.generate(data, cancellation_event)

                logger.info("✅ [worker] Non-streaming inference completed")
                return result

        except TimeoutError:
            logger.error(
                f"❌ [worker] [{request_id}] Queue timeout after {timeout_hint}s"
            )
            raise EngineError(
                code="QUEUE_TIMEOUT",
                message=f"Timeout waiting for inference slot ({timeout_hint}s)",
            )
        except asyncio.CancelledError:
            logger.info(f"🛑 [worker] [{request_id}] Cancelled while queued")
            raise EngineError(
                code="CANCELLED", message="Request cancelled while queued"
            )
        except EngineError:
            raise
        except Exception as e:
            if cancellation_event.is_set():
                logger.info(f"🛑 [worker] Inference cancelled for request {request_id}")
                raise EngineError(code="CANCELLED", message="Inference cancelled")
            logger.error(f"❌ [worker] Non-streaming inference failed: {e}")
            raise self._map_exception_to_engine_error(e)

        finally:
            # CRITICAL: Release gate if acquired (prevents gate slot leaks)
            if gate_acquired:
                await asyncio.shield(gate.release())
            # Cleanup (centralized)
            await cleanup_stream_entry(request_id, reason="request_complete")
