"""Cancellation and RPC call mixin for WorkerController."""

from typing import Any

from ._runtime import _get_resource_tracker, logger


class CancelRpcMixin:
    """Streaming cancellation, work cancellation, and worker RPC calls."""

    async def cancel_streaming_inference(
        self, model_id: str, stream_id: str, reason: str = "explicit_cancellation"
    ) -> bool:
        return await self._inference_cancellation.cancel_streaming_inference(
            model_id, stream_id, reason
        )

    async def cancel_current_stream(self, model_id: str) -> bool:
        return await self._inference_cancellation.cancel_current_stream(model_id)

    async def cancel_work(
        self,
        model_id: str,
        stream_id: str | None = None,
        reason: str = "explicit_cancellation",
    ) -> bool:
        """
        Cancel active work on a model.

        Event-driven state update via STREAM_CANCELLED when RPC succeeds.
        On RPC failure, sets ERROR (worker may still be busy).

        Invariant: ∀ successful cancellation, idle update via event consumer
                   (∃! writer).
        """
        success = await self._cancel_work_rpc(model_id, stream_id, reason)

        if success:
            from ..cancellation import emit_stream_cancelled_or_force_idle

            await emit_stream_cancelled_or_force_idle(
                model_id, stream_id, reason, event_bus=self.event_bus
            )
        else:
            await self._handle_rpc_cancel_failure(model_id, reason)

        return success

    async def _cancel_work_rpc(
        self, model_id: str, stream_id: str | None, reason: str
    ) -> bool:
        """Call RPC cancellation (no tracker updates)."""
        return await self._inference_cancellation.cancel_work(
            model_id, stream_id, reason
        )

    async def _handle_rpc_cancel_failure(self, model_id: str, reason: str) -> None:
        """
        Handle RPC cancellation failure: mark model as ERROR.

        Worker may still be busy; forcing idle would allow concurrent requests.
        """
        logger.error(f"❌ RPC cancellation failed for {model_id}, marking as ERROR")
        _get_resource_tracker().set_model_error(
            model_id, f"Cancellation RPC failed: {reason}"
        )

    async def _call_rpc(
        self, model_id: str, method: str, params: dict[str, Any], timeout: float = 300.0
    ) -> dict[str, Any]:
        """
        Internal: Call an RPC method on the worker for the specified model.

        Args:
            model_id: Model ID (must be loaded)
            method: RPC method name to call
            params: Parameters to pass to the RPC method
            timeout: Request timeout in seconds

        Returns:
            RPC response data

        Raises:
            RuntimeError: If model not loaded or RPC fails
        """
        sup = self._process_state.get_supervisor(model_id)
        if not sup:
            raise RuntimeError(f"No supervisor found for model {model_id}")

        if not sup._http_client:
            raise RuntimeError(f"HTTP client not initialized for model {model_id}")

        return await sup._inference_rpc_call(method, params, timeout=timeout)

    async def call_rpc(
        self, model_id: str, method: str, params: dict[str, Any], timeout: float = 300.0
    ) -> Any:
        """
        Public: Call an RPC method on the worker for the specified model.

        Args:
            model_id: Model ID (must be loaded)
            method: RPC method name to call
            params: Parameters to pass to the RPC method
            timeout: Request timeout in seconds

        Returns:
            RPC response data

        Raises:
            RuntimeError: If model not loaded or RPC fails
        """
        return await self._call_rpc(model_id, method, params, timeout)
