"""
RequestExecutor: top-level request orchestration for the nonstreaming proxy.

Part of the `nonstreaming/executor` subpackage. Owns the request lifecycle —
gateway selection, capacity management, routing-key tracking, token management
— and delegates actual forwarding to `federated_execution` and `embeddings`.

Single responsibility: orchestrate; do not contain forwarding logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope

from ..bypass_mode import execute_bypass_mode
from ..context import RequestContext
from ..gateway_selection import select_gateway_and_load_model
from .federated_execution import execute_federated_request

if TYPE_CHECKING:
    from model_id import ModelId


logger = get_logger(__name__)


class RequestExecutor:
    """
    Orchestrates the full lifecycle of a single inference request.

    Dependencies injected at construction time; stateless per request.
    Every exit path releases the capacity token and routing key.
    """

    def __init__(
        self,
        gateway_url: str,
        monitor,
        forward_request_func,
        forward_streaming_request_func,
        gateway_manager,
        http_client=None,
        token_manager=None,
        model_manager=None,
        event_bus=None,
        federation_forwarder=None,
        federation_circuit_breaker=None,
        token_allocation_policy=None,
        federated_manager=None,
        federated_load_orchestrator=None,
        routing_config: dict | None = None,
        stability_tracker=None,
        transformation_engine=None,
        federation_integration=None,
        capacity_pool=None,
    ):
        self.gateway_url = gateway_url
        self.monitor = monitor
        self.forward_request = forward_request_func
        self.forward_streaming_request = forward_streaming_request_func
        self.gateway_manager = gateway_manager
        self.http_client = http_client
        self.token_manager = token_manager
        self.model_manager = model_manager
        self.event_bus = event_bus
        # Explicit assignment — NO getattr fallback
        self._federation_forwarder = federation_forwarder
        self._federation_circuit_breaker = federation_circuit_breaker
        self._token_allocation_policy = token_allocation_policy
        self._federated_manager = federated_manager
        self._federated_load_orchestrator = federated_load_orchestrator
        self._routing_config = routing_config
        self._stability_tracker = stability_tracker
        self._transformation_engine = transformation_engine
        self._federation_integration = federation_integration
        self._capacity_pool = capacity_pool

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def execute_request(self, context: RequestContext) -> Response:
        """Route to bypass or normal mode based on context flags."""
        if context.bypass_transformations:
            return await self._execute_bypass_mode(context)
        return await self._execute_normal_mode(context)

    async def execute_embedding_request(
        self,
        model_id: str,
        request_body: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Execute an embedding request via federation.

        Delegates to `embeddings.execute_embedding_request`, passing bound
        methods as callables so the embedding module remains decoupled from
        `RequestExecutor`.

        Args:
            model_id: String model identifier (parsed at boundary).
            request_body: Embedding request body dict.
            request_id: Optional caller-provided ID; UUID generated if absent.

        Returns:
            Embedding response dict from the gateway.
        """
        from .embeddings import (
            execute_embedding_request as _exec,
        )
        from .embeddings import (
            forward_embedding_request,
        )

        async def _forward(*, gateway, request_body, request_id):  # type: ignore[override]
            return await forward_embedding_request(
                gateway,
                request_body,
                request_id,
                federation_integration=self._federation_integration,
                federation_forwarder=self._federation_forwarder,
            )

        async def _oom_recovery(*, gateway, model_id, request_id):
            from .oom_recovery import attempt_oom_recovery

            request_tracker = None
            if self._federation_integration is not None:
                request_tracker = self._federation_integration.request_tracker

            return await attempt_oom_recovery(
                gateway=gateway,
                model_id=model_id,
                federated_manager=self._federated_manager,
                federation_forwarder=self._federation_forwarder,
                request_tracker=request_tracker,
                event_bus=self.event_bus,
                request_id=request_id,
            )

        return await _exec(
            model_id,
            request_body,
            request_id,
            select_gateway_fn=self._select_gateway_and_load_model,
            release_routing_key_fn=self._release_routing_key_on_error,
            release_capacity_token_fn=self._release_capacity_token,
            forward_embedding_fn=_forward,
            event_bus=self.event_bus,
            oom_recovery_fn=_oom_recovery if self._federated_manager else None,
        )

    # ------------------------------------------------------------------
    # Internal: gateway selection and capacity management
    # ------------------------------------------------------------------

    async def _select_gateway_and_load_model(self, context: RequestContext) -> None:
        """Select a gateway and ensure the model is loaded on it."""
        request_tracker = None
        if self._federation_integration is not None:
            request_tracker = self._federation_integration.request_tracker

        _ = await select_gateway_and_load_model(
            context=context,
            model_manager=self.model_manager,
            gateway_manager=self.gateway_manager,
            event_bus=self.event_bus,
            federated_manager=self._federated_manager,
            federated_load_orchestrator=self._federated_load_orchestrator,
            routing_config=self._routing_config,
            stability_tracker=self._stability_tracker,
            routing_key_tracker=request_tracker,
            capacity_pool=self._capacity_pool,
            circuit_breaker=self._federation_circuit_breaker,
        )

    def _release_routing_key_on_error(self, request_id: str) -> None:
        """
        Release the routing key when a request fails between load and forward.

        The load orchestrator tracks routing_key for eviction protection on
        load success.  Normally MasterRequestTracker.forward() releases it in
        its finally block.  If the request fails before forward() runs (e.g.
        token counting error), the key leaks and permanently blocks eviction.

        Idempotent — safe to call even if forward() already released the key.
        """
        if self._federation_integration is None:
            return
        tracker = self._federation_integration.request_tracker
        if tracker is None:
            return
        released = tracker.release_routing_key(request_id)
        if released:
            logger.warning(
                "🔓 Released routing key for request %s "
                "(failed between load and forward)",
                request_id[:8],
            )

    async def _release_capacity_token(self, context: RequestContext) -> None:
        """Release the capacity token held for this request. Idempotent."""
        if context.capacity_token:
            await context.capacity_token.release()
            context.capacity_token = None

    # ------------------------------------------------------------------
    # Internal: execution modes
    # ------------------------------------------------------------------

    async def _execute_bypass_mode(self, context: RequestContext) -> Response:
        """Execute in bypass mode (no transformation or token management)."""
        await self._select_gateway_and_load_model(context)

        try:
            model_metadata = await self._get_model_configuration_for_monitoring(
                context.selected_model
            )
            response = await execute_bypass_mode(
                context=context,
                gateway_url=self.gateway_url,
                monitor=self.monitor,
                forward_request_func=self.forward_request,
                forward_streaming_request_func=self.forward_streaming_request,
                model_metadata=model_metadata,
            )
            return response
        except Exception:
            self._release_routing_key_on_error(context.request_id)
            raise
        finally:
            await self._release_capacity_token(context)

    async def _execute_normal_mode(self, context: RequestContext) -> Response:
        """
        Execute with full transformation and federated forwarding.

        INVARIANT:
            ∀ request: forwarded via MasterRequestTracker (atomic capacity)
            ∀ routing_key tracked by load: released on ANY exit path
            ∧ ∀ exception (including CancelledError/BaseException): capacity released
        """
        logger.debug(f"Forwarding request for model {context.selected_model}")

        await self._select_gateway_and_load_model(context)

        fed_gateway = context.federated_gateway
        if fed_gateway is None:
            raise HTTPException(
                status_code=500,
                detail=error_envelope(
                    code=ErrorCode.UNEXPECTED_ERROR,
                    message="Federated gateway not set in context after selection",
                    source="master",
                    retryable=False,
                    data={},
                ),
            )

        try:
            from ..token_management import apply_federated_token_management

            try:
                await apply_federated_token_management(
                    context,
                    self._token_allocation_policy,
                    context.federated_gateway,
                    self._federation_forwarder,
                )
            except Exception as e:
                if fed_gateway and self.event_bus:
                    await self._emit_token_counting_failed(
                        context, fed_gateway.gateway_id, str(e)
                    )
                raise

            response = await execute_federated_request(
                context=context,
                federation_forwarder=self._federation_forwarder,
                federation_circuit_breaker=self._federation_circuit_breaker,
                transformation_engine=self._transformation_engine,
                federation_integration=self._federation_integration,
                event_bus=self.event_bus,
                release_capacity_token=self._release_capacity_token,
                federated_manager=self._federated_manager,
            )

            # Non-streaming: release token now (response fully built).
            # Streaming: released inside stream_generator_with_cleanup's finally block.
            if not isinstance(response, StreamingResponse):
                try:
                    await self._release_capacity_token(context)
                except Exception:
                    logger.warning(
                        "Failed to release capacity token for request %s",
                        context.request_id,
                        exc_info=True,
                    )

            return response

        except Exception:
            self._release_routing_key_on_error(context.request_id)
            await self._release_capacity_token(context)
            raise
        except BaseException:
            # CancelledError (client disconnect) is BaseException, not Exception.
            # Must release here or in_flight leaks permanently — available slots
            # drain to 0 and the gateway appears saturated while actually idle.
            await self._release_capacity_token(context)
            raise

    # ------------------------------------------------------------------
    # Internal: observability helpers
    # ------------------------------------------------------------------

    async def _emit_token_counting_failed(
        self,
        context: RequestContext,
        gateway_id: str,
        error: str,
    ) -> None:
        """Fire-and-forget `token.counting.failed` event."""
        import asyncio

        from src.scheduling.events import TokenCountingFailed

        asyncio.create_task(
            self.event_bus.publish_async_nowait(
                TokenCountingFailed(
                    request_id=context.request_id,
                    model_id=context.selected_model.routing_key,
                    gateway_id=gateway_id,
                    error=error,
                )
            )
        )

    async def _get_model_configuration_for_monitoring(
        self, model_id: ModelId
    ) -> dict[str, Any]:
        """
        Return cached model config for bypass-mode monitoring (non-blocking).

        Schedules a background fetch if not cached; returns {} immediately.
        Invariant: ∀ call: blocks_request_path = False
        """
        from ..monitoring_config import (
            get_cached_configuration_for_monitoring,
            schedule_background_configuration_fetch,
        )

        cached = get_cached_configuration_for_monitoring(self.gateway_manager, model_id)
        if cached:
            return cached

        schedule_background_configuration_fetch(self.gateway_manager, model_id)
        return {}
