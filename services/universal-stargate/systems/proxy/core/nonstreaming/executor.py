"""
Request execution module - handles forwarding and response processing.

This module is responsible for:
- Forwarding prepared requests to the gateway
- Handling streaming vs non-streaming responses
- Applying response transformations
- Monitoring and logging
"""

import time
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.responses import Response
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope

from ..endpoint_category import derive_endpoint_category
from .bypass_mode import execute_bypass_mode
from .context import RequestContext
from .gateway_selection import select_gateway_and_load_model

if TYPE_CHECKING:
    from model_id import ModelId

    from systems.federation.common.config.schema import EndpointCategory
    from systems.federation.common.types import FederatedGateway

logger = get_logger(__name__)


class RequestExecutor:
    """Handles request execution and response processing."""

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
        # Explicit assignment - NO getattr fallback
        self._federation_forwarder = federation_forwarder
        self._federation_circuit_breaker = federation_circuit_breaker
        self._token_allocation_policy = token_allocation_policy
        self._federated_manager = federated_manager
        self._federated_load_orchestrator = federated_load_orchestrator
        self._routing_config = routing_config
        self._stability_tracker = stability_tracker
        self._transformation_engine = transformation_engine
        self._federation_integration = federation_integration

    async def execute_request(self, context: RequestContext) -> Response:
        """
        Execute a prepared request and return the response.

        This handles both bypass mode and normal mode, streaming and non-streaming.
        """
        if context.bypass_transformations:
            return await self._execute_bypass_mode(context)
        else:
            return await self._execute_normal_mode(context)

    async def _execute_bypass_mode(self, context: RequestContext) -> Response:
        """Execute request in bypass mode."""
        await self._select_gateway_and_load_model(context)

        # Get model configuration for monitoring (can pass ModelId directly)
        model_metadata = await self._get_model_configuration_for_monitoring(
            context.selected_model
        )

        return await execute_bypass_mode(
            context=context,
            gateway_url=self.gateway_url,
            monitor=self.monitor,
            forward_request_func=self.forward_request,
            forward_streaming_request_func=self.forward_streaming_request,
            model_metadata=model_metadata,
        )

    async def _select_gateway_and_load_model(self, context: RequestContext) -> None:
        """Ensure gateway is selected and model is loaded."""
        # Get request_tracker from federation integration.
        # Implements RoutingKeyTracker protocol.
        # None is valid (Edge/Remote mode, or Master without tracker).
        request_tracker = None
        if self._federation_integration is not None:
            request_tracker = self._federation_integration.request_tracker

        # Get admission_queue from proxy/federation_integration if available
        # Admission control: CapacityLedger in systems/routing/capacity/
        admission_queue = None
        if self._federation_integration and hasattr(
            self._federation_integration, "_proxy"
        ):
            admission_queue = getattr(
                self._federation_integration._proxy, "admission_queue", None
            )

        gateway_name, _ = await select_gateway_and_load_model(
            context=context,
            model_manager=self.model_manager,
            gateway_manager=self.gateway_manager,
            event_bus=self.event_bus,
            federated_manager=self._federated_manager,
            federated_load_orchestrator=self._federated_load_orchestrator,
            routing_config=self._routing_config,
            stability_tracker=self._stability_tracker,
            compute_type_tracker=request_tracker,
            routing_key_tracker=request_tracker,
            admission_queue=admission_queue,
        )

    async def _execute_normal_mode(self, context: RequestContext) -> Response:
        """
        Execute request with full transformations.

        Post-unification: All requests forwarded via federation.

        INVARIANT:
            ∀ request: forwarded via MasterRequestTracker (atomic capacity)
            ∀ capacity: reserved atomically during feasibility check
        """
        logger.debug(f"Forwarding request for model {context.selected_model}")

        # Step 1: Select gateway (federated)
        await self._select_gateway_and_load_model(context)

        # Step 2: Validate gateway was selected
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

        # Step 3: Apply token management via federation forwarder
        from .token_management import apply_federated_token_management

        await apply_federated_token_management(
            context,
            self._token_allocation_policy,
            context.federated_gateway,
            self._federation_forwarder,
        )

        # Step 4: Execute federated request
        return await self._execute_federated_request(context)

    async def _get_model_configuration_for_monitoring(
        self, model_id: "ModelId"
    ) -> dict[str, Any]:
        """Get model configuration for monitoring (non-blocking).

        Args:
            model_id: Model ID object (parsed at request boundary)

        Returns:
            Monitoring dict if cached, empty dict otherwise (background fetch scheduled)

        Invariant: ∀ call: blocks_request_path = False
        """
        from .monitoring_config import (
            get_cached_configuration_for_monitoring,
            schedule_background_configuration_fetch,
        )

        cached = get_cached_configuration_for_monitoring(self.gateway_manager, model_id)
        if cached:
            return cached

        schedule_background_configuration_fetch(self.gateway_manager, model_id)
        return {}

    async def _execute_federated_request(self, context: RequestContext) -> Response:
        """
        Execute request on a federated gateway via FederatedRequestForwarder.

        INVARIANT:
            ∀ federated_request:
                circuit_allows(gateway) ⟹ forward
                ∧ capacity_tracked_by_MasterRequestTracker
                ∧ (¬streaming ∧ success) ⟹ record_success
                ∧ (¬streaming ∧ failure) ⟹ record_failure

        NOTE: Streaming requests do NOT update circuit breaker state because
        success/failure is determined after response headers are sent. This is
        a known limitation - circuit breaker tracks non-streaming health only.
        """
        fed_gateway = context.federated_gateway
        if fed_gateway is None:
            raise HTTPException(
                status_code=500,
                detail=error_envelope(
                    code=ErrorCode.UNEXPECTED_ERROR,
                    message="Federated gateway not set in context",
                    source="master",
                    retryable=False,
                    data={},
                ),
            )

        if self._federation_forwarder is None:
            raise HTTPException(
                status_code=503,
                detail=error_envelope(
                    code=ErrorCode.CONFIGURATION_ERROR,
                    message="Federation forwarder not available (Master mode required)",
                    source="master",
                    retryable=False,
                    data={},
                ),
            )

        # Check circuit breaker (if available)
        if self._federation_circuit_breaker:
            if not await self._federation_circuit_breaker.should_allow_request(
                fed_gateway.gateway_id
            ):
                raise HTTPException(
                    status_code=503,
                    detail=error_envelope(
                        code=ErrorCode.RESOURCE_UNAVAILABLE,
                        message=(
                            f"Federated gateway {fed_gateway.gateway_id} circuit open"
                        ),
                        source="master",
                        retryable=True,
                        data={"gateway_id": fed_gateway.gateway_id},
                    ),
                )

        # Build request body
        request_body = context.original_request.copy()
        if context.modified_request:
            request_body.update(context.modified_request)

        # Write after-transformation snapshot if debugging enabled
        from ...debug.request_snapshots import write_request_snapshot

        await write_request_snapshot(request_body, context.request_id, stage="after")

        # INV: single request identity — request_id is the sole key
        request_id = context.request_id
        hop_count = 1

        # Use endpoint category from routing to ensure consistency with reservation
        # CRITICAL: If we re-derive, capacity tracking leaks when derivation differs
        endpoint_category = context.routing_endpoint_category
        if endpoint_category is None:
            # Fallback if routing didn't set it (shouldn't happen)
            logger.warning(
                "⚠️ routing_endpoint_category not set, deriving from request path"
            )
            endpoint_category = derive_endpoint_category(request=context.http_request)

        # Get input_schema for transformation hint
        model_id = context.selected_model
        input_schema = "messages"  # Default
        if fed_gateway.model_resources:
            model_metadata = fed_gateway.model_resources.get(model_id)
            if model_metadata:
                input_schema = model_metadata.get("input_schema", "messages")

        hints = {"input_schema": input_schema}

        # Add timeout hint if pipeline provided one
        if context.request_timeout_hint:
            hints["timeout"] = context.request_timeout_hint

        # Apply transformation on Master before forwarding if input_schema=prompt
        if self._transformation_engine and input_schema == "prompt":
            from systems.transformations import OutputFormat

            messages = request_body.get("messages")
            if messages:
                try:
                    result = self._transformation_engine.transform(
                        messages=messages,
                        model=model_id,
                        target_format=OutputFormat.PROMPT,
                    )

                    # Replace messages with prompt in request body
                    request_body = request_body.copy()
                    del request_body["messages"]
                    request_body["prompt"] = result.content

                    prompt_len = len(str(result.content))
                    logger.info(
                        f"🔄 Master applied transformation for {model_id} "
                        f"(input_schema={input_schema}): {prompt_len} chars"
                    )

                except Exception as e:
                    logger.error(
                        f"❌ Master transformation failed for {model_id}: {e}. "
                        "Forwarding original request (may fail at Edge/Gateway)."
                    )

        import json

        logger.info(
            f"📤 REQUEST BODY (to Federated Gateway): {json.dumps(request_body)}"
        )

        logger.info(
            f"🌐 Forwarding to federated gateway {fed_gateway.gateway_id} "
            f"via {fed_gateway.remote_stargate_id} (request={request_id[:8]}, "
            f"hints={hints})"
        )

        # Streaming: Wrap stream to release slot when done
        if context.client_wants_streaming:
            return await self._execute_federated_streaming(
                context,
                fed_gateway,
                request_body,
                request_id,
                hop_count,
                endpoint_category,
                hints,
            )

        # Non-streaming: Release slot in finally block
        try:
            response = await self._execute_federated_nonstreaming(
                context,
                fed_gateway,
                request_body,
                request_id,
                hop_count,
                endpoint_category,
                hints,
            )

            if self._federation_circuit_breaker:
                await self._federation_circuit_breaker.record_success(
                    fed_gateway.gateway_id
                )

            return response

        except HTTPException as e:
            if self._federation_circuit_breaker and e.status_code >= 500:
                await self._federation_circuit_breaker.record_failure(
                    fed_gateway.gateway_id, str(e.detail)
                )
            raise
        except Exception as e:
            if self._federation_circuit_breaker:
                await self._federation_circuit_breaker.record_failure(
                    fed_gateway.gateway_id, str(e)
                )
            raise

    async def _forward_via_tracker_or_forwarder(
        self,
        fed_gateway: "FederatedGateway",
        request_body: dict[str, Any],
        hop_count: int,
        request_id: str,
        endpoint_category: "EndpointCategory",
        model_id: str,
        hints: dict[str, Any] | None,
        context: RequestContext | None = None,
    ) -> tuple[dict[str, Any], dict[str, str], int]:
        """
        Forward request using tracker if available, forwarder otherwise.

        Master mode uses request_tracker for atomic capacity tracking.
        Edge/Remote modes fall back to direct forwarder.

        Returns:
            Tuple of (response_content, response_headers, status_code)
        """
        # Get request_tracker from federation_integration
        request_tracker = None
        if self._federation_integration is not None:
            request_tracker = self._federation_integration.request_tracker

        if request_tracker is None:
            # Fallback to direct forwarder (Edge/Remote mode)
            forwarder = self._federation_forwarder
            if forwarder is None:
                raise HTTPException(
                    status_code=503,
                    detail=error_envelope(
                        code=ErrorCode.CONFIGURATION_ERROR,
                        message=(
                            "Federation forwarder not available (Master mode required)"
                        ),
                        source="master",
                        retryable=False,
                        data={},
                    ),
                )

            # Direct forwarder call (no capacity tracking)
            # Returns httpx.Response
            response = await forwarder.forward_request(
                fed_gateway, request_body, hop_count, request_id, hints=hints
            )
            response_content = response.json()
            response_status_code = response.status_code

            # Extract headers from httpx.Response
            response_headers = self._extract_remote_headers(response)

            return response_content, response_headers, response_status_code
        else:
            response_content = await request_tracker.forward(
                gateway=fed_gateway,
                request_body=request_body,
                hop_count=hop_count,
                endpoint_category=endpoint_category,
                model_id=model_id,
                hints=hints,
                request_id=request_id,
            )
            # Note: request_tracker doesn't preserve remote headers
            # This is acceptable for Master mode - headers are informational only
            return response_content, {}, 200

    def _extract_remote_headers(self, response) -> dict[str, str]:
        """
        Extract and namespace headers from remote httpx.Response.

        Remote headers are namespaced with x-federated- prefix to avoid conflicts.

        Args:
            response: httpx.Response from remote gateway

        Returns:
            Dict of namespaced headers
        """
        headers_to_preserve = [
            "x-request-id",
            "x-correlation-id",
            "x-response-time-ms",
            "x-model-id",
            "x-gateway-id",
        ]
        response_headers: dict[str, str] = {}

        for header in headers_to_preserve:
            if header in response.headers:
                # Namespace remote headers with x-federated-
                namespaced = (
                    f"x-federated-{header[2:]}" if header.startswith("x-") else header
                )
                response_headers[namespaced] = response.headers[header]

        return response_headers

    def _apply_content_filter_to_response(
        self, response_content: dict[str, Any], content_filter, model_name: str
    ) -> dict[str, Any]:
        """
        Apply content filter to chat completion response.

        Filters analysis sections from model responses if configured.

        Args:
            response_content: Response dict from gateway
            content_filter: ContentFilter instance or None
            model_name: Model identifier for logging

        Returns:
            Response dict with filtered content (modified in-place)
        """
        if not content_filter or not isinstance(response_content, dict):
            return response_content

        if "choices" in response_content:
            choices = response_content.get("choices", [])
            if choices and "message" in choices[0]:
                content_text = choices[0]["message"].get("content", "")

                if content_text:
                    # Use the simplified filter method for non-streaming
                    filtered_content = content_filter.filter_content(content_text)

                    # Update the response if content was changed
                    if filtered_content != content_text:
                        response_content["choices"][0]["message"]["content"] = (
                            filtered_content
                        )
                        logger.info(
                            f"Applied analysis filter to federated "
                            f"non-streaming response: {model_name}"
                        )

        return response_content

    def _prepare_federation_headers(
        self,
        fed_gateway: "FederatedGateway",
        base_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """
        Prepare response headers with federation metadata.

        Adds federation source and gateway identification headers.

        Args:
            fed_gateway: Federated gateway that handled request
            base_headers: Optional base headers to extend

        Returns:
            Dict of headers including federation metadata
        """
        headers = (base_headers or {}).copy()
        headers["x-federation-source"] = fed_gateway.remote_stargate_id
        headers["x-federation-gateway"] = fed_gateway.gateway_id
        return headers

    async def _execute_federated_streaming(
        self,
        context: RequestContext,
        fed_gateway: "FederatedGateway",
        request_body: dict[str, Any],
        request_id: str,
        hop_count: int,
        endpoint_category: "EndpointCategory",
        hints: dict[str, Any] | None = None,
    ) -> Response:
        """
        Forward streaming request to federated gateway.

        Routing:
            - Master mode: Uses request_tracker for atomic capacity tracking
            - Edge/Remote mode: Falls back to direct forwarder

        Converts Remote's NDJSON stream to client-facing SSE format.

        Returns:
            TrackedStreamingResponse with SSE chunks

        Invariants:
            - Remote→Master uses NDJSON framing (preserves line boundaries)
            - Master→Client converts to SSE via ChunkProcessor
            - On error, log and terminate stream (do NOT inject SSE error events)
            - Capacity cleanup: MasterRequestTracker decrements in finally
        """
        import httpx

        from ...utils.analysis_section_filter import create_content_filter
        from ..common import ChunkProcessor
        from ..streaming.response_tracker import TrackedStreamingResponse

        # Create content filter for analysis section filtering
        model_name = str(context.selected_model)
        content_filter = create_content_filter(model_name, context.request_id)

        if content_filter:
            logger.info(
                f"✅ Analysis filter created for federated streaming: {model_name}"
            )

        async def stream_generator_with_cleanup():
            """Convert Remote NDJSON stream → client SSE stream + cleanup slot."""
            chunk_processor = ChunkProcessor(content_filter=content_filter)
            received_count = 0
            yielded_count = 0
            try:
                # Get request_tracker from federation_integration
                request_tracker = None
                if self._federation_integration is not None:
                    request_tracker = self._federation_integration.request_tracker

                # Use request_tracker for atomic capacity tracking if available
                if request_tracker is None:
                    # Fallback to direct forwarder if tracker not available
                    # (Edge/Remote mode, or testing)
                    forwarder = self._federation_forwarder
                    if forwarder is None:
                        raise HTTPException(
                            status_code=503,
                            detail=error_envelope(
                                code=ErrorCode.CONFIGURATION_ERROR,
                                message=(
                                    "Federation forwarder not available "
                                    "(Master mode required)"
                                ),
                                source="master",
                                retryable=False,
                                data={},
                            ),
                        )

                    # Direct forwarder call (no capacity tracking)
                    stream = forwarder.forward_request_stream(
                        fed_gateway,
                        request_body,
                        hop_count,
                        request_id,
                        hints=hints,
                    )
                else:
                    # Use tracker for capacity-aware forwarding
                    stream = request_tracker.forward_stream(
                        gateway=fed_gateway,
                        request_body=request_body,
                        hop_count=hop_count,
                        endpoint_category=endpoint_category,
                        model_id=str(context.selected_model),
                        hints=hints,
                        request_id=request_id,
                    )

                async for chunk in stream:
                    received_count += 1
                    processed = chunk_processor.process_chunk(chunk, context=None)
                    if processed is None:
                        continue
                    if processed.should_yield and processed.sse_format:
                        yielded_count += 1
                        yield processed.sse_format
                    if processed.is_done:
                        break

                # Log stream completion with diagnostics
                if yielded_count == 0:
                    logger.error(
                        f"❌ [FED:{request_id[:8]}] Stream completed with "
                        f"ZERO yielded chunks (received={received_count}) "
                        f"for {model_name} on {fed_gateway.gateway_id}"
                    )
                else:
                    logger.info(
                        f"✅ [FED:{request_id[:8]}] Stream completed "
                        f"(received={received_count}, yielded={yielded_count}) "
                        f"for {model_name}"
                    )
            except httpx.HTTPStatusError as e:
                # Log error, terminate stream (no SSE injection per invariant)
                logger.exception(
                    f"Federated streaming HTTP error "
                    f"(received={received_count}, yielded={yielded_count})",
                    extra={
                        "request_id": request_id,
                        "gateway_id": fed_gateway.gateway_id,
                        "status_code": e.response.status_code,
                    },
                )
                # Stream terminates - client sees incomplete response
            except Exception:
                logger.exception(
                    f"Federated streaming error "
                    f"(received={received_count}, yielded={yielded_count})",
                    extra={"request_id": request_id},
                )

        return TrackedStreamingResponse(
            stream_generator_with_cleanup(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
            request_id=request_id,
            model=str(context.selected_model),
        )

    async def _execute_federated_nonstreaming(
        self,
        context: RequestContext,
        fed_gateway: "FederatedGateway",
        request_body: dict[str, Any],
        request_id: str,
        hop_count: int,
        endpoint_category: "EndpointCategory",
        hints: dict[str, Any] | None = None,
    ) -> Response:
        """
        Forward non-streaming request to federated gateway.

        Routing:
            - Master mode: Uses request_tracker for atomic capacity tracking
            - Edge/Remote mode: Falls back to direct forwarder

        Returns:
            JSONResponse with:
            - Content: Remote response (dict)
            - Status: 200 (tracker path) or remote status (forwarder path)
            - Headers: Federation metadata + remote headers (forwarder path only)

        Error semantics:
            - 4xx/5xx from remote → 502 Bad Gateway
            - Timeout → 504 Gateway Timeout

        Note:
            request_tracker.forward() returns dict directly (not httpx.Response).
            Header preservation only works in forwarder fallback path.

        Invariant:
            ∀ success_response: status_code = remote_status_code
            ∧ headers preserved with x-federated- namespace
        """
        import httpx
        from fastapi.responses import JSONResponse

        from ...utils.analysis_section_filter import create_content_filter

        # Create content filter for analysis section filtering
        model_name = str(context.selected_model)
        content_filter = create_content_filter(model_name, context.request_id)

        if content_filter:
            logger.info(
                f"✅ Analysis filter created for federated non-streaming: {model_name}"
            )

        try:
            # Forward request via tracker or forwarder
            (
                response_content,
                response_headers,
                response_status_code,
            ) = await self._forward_via_tracker_or_forwarder(
                fed_gateway,
                request_body,
                hop_count,
                request_id,
                endpoint_category,
                str(context.selected_model),
                hints,
                context,
            )

            # Write response snapshot BEFORE filtering (from federated gateway)
            from ...debug.request_snapshots import write_response_snapshot

            await write_response_snapshot(
                response_content, context.request_id, stage="response-from-gateway"
            )

            # Apply content filter if needed
            response_content = self._apply_content_filter_to_response(
                response_content, content_filter, model_name
            )

            # Prepare final headers with federation metadata
            final_headers = self._prepare_federation_headers(
                fed_gateway, response_headers
            )

            # Write final response snapshot AFTER filtering (to client)
            await write_response_snapshot(
                response_content, context.request_id, stage="response-to-client"
            )

            # Emit execution completed for slot tracking
            from systems.proxy.core.lifecycle import emit_execution_completed

            await emit_execution_completed(
                event_bus=self.event_bus,
                url=fed_gateway.remote_stargate_url,
                model_id=str(context.selected_model),
                request_id=context.request_id,
                gateway_id=fed_gateway.gateway_id,
            )

            return JSONResponse(
                content=response_content,
                status_code=response_status_code,
                headers=final_headers,
            )

        except httpx.HTTPStatusError as e:
            # Remote returned 4xx/5xx - map to 502 (bad gateway)
            logger.error(
                f"Federated request HTTP error: {e.response.status_code} "
                f"for {fed_gateway.gateway_id}"
            )
            # Emit execution completed for slot tracking (error path)
            from systems.proxy.core.lifecycle import emit_execution_completed

            await emit_execution_completed(
                event_bus=self.event_bus,
                url=fed_gateway.remote_stargate_url,
                model_id=str(context.selected_model),
                request_id=context.request_id,
                gateway_id=fed_gateway.gateway_id,
            )
            raise HTTPException(
                status_code=502,
                detail=error_envelope(
                    code=ErrorCode.UNEXPECTED_ERROR,
                    message=f"Remote gateway error: {e.response.status_code}",
                    source="master",
                    retryable=True,
                    data={
                        "status_code": e.response.status_code,
                        "gateway_id": fed_gateway.gateway_id,
                    },
                ),
            )
        except httpx.TimeoutException:
            logger.error(f"Federated request timeout for {fed_gateway.gateway_id}")
            # Emit execution completed for slot tracking (timeout path)
            from systems.proxy.core.lifecycle import emit_execution_completed

            await emit_execution_completed(
                event_bus=self.event_bus,
                url=fed_gateway.remote_stargate_url,
                model_id=str(context.selected_model),
                request_id=context.request_id,
                gateway_id=fed_gateway.gateway_id,
            )
            raise HTTPException(
                status_code=504,
                detail=error_envelope(
                    code=ErrorCode.REQUEST_TIMEOUT,
                    message="Remote gateway timeout",
                    source="master",
                    retryable=True,
                    data={"gateway_id": fed_gateway.gateway_id},
                ),
            )
        except Exception as e:
            logger.error(f"Federated request error: {e}")
            # Emit execution completed for slot tracking (generic error path)
            from systems.proxy.core.lifecycle import emit_execution_completed

            await emit_execution_completed(
                event_bus=self.event_bus,
                url=fed_gateway.remote_stargate_url,
                model_id=str(context.selected_model),
                request_id=context.request_id,
                gateway_id=fed_gateway.gateway_id,
            )
            raise HTTPException(
                status_code=502,
                detail=error_envelope(
                    code=ErrorCode.UNEXPECTED_ERROR,
                    message=f"Federated gateway error: {e}",
                    source="master",
                    retryable=True,
                    data={"gateway_id": fed_gateway.gateway_id},
                ),
            )

    async def execute_embedding_request(
        self,
        model_id: str,
        request_body: dict,
        request_id: str | None = None,
    ) -> dict:
        """
        Execute embedding request via federation.

        Args:
            model_id: Model identifier
            request_body: Embedding request body
            request_id: Request ID

        Returns:
            Embedding response from Gateway
        """
        from model_id import ModelId

        from .context import RequestContext

        # Parse model_id at boundary
        parsed_model_id = ModelId.parse(model_id)

        resolved_request_id = request_id or str(uuid.uuid4())

        # Import EndpointCategory for explicit category setting
        from systems.federation.common.config.schema import EndpointCategory

        # Create context with ALL required arguments
        context = RequestContext(
            request_id=resolved_request_id,
            start_time=time.time(),
            selected_model=parsed_model_id,
            original_request=request_body,
            raw_client_fields={},  # Embeddings: no chat fields to preserve
            user_params={},  # Embeddings: no generation params
            middleware_actions=[],  # Embeddings: no middleware mutations
            bypass_transformations=True,
            disable_profile=True,
            skip_token_counting=True,
            http_request=None,
            chat_request=None,
            selected_gateway=None,
        )

        # CRITICAL: Pre-set endpoint category for embeddings
        # Routing cannot derive from http_request (None for programmatic calls)
        # This ensures capacity reservation uses correct key (embedding, not generation)
        context.routing_endpoint_category = EndpointCategory.EMBEDDING

        # Route to appropriate gateway
        await self._select_gateway_and_load_model(context)

        fed_gateway = context.federated_gateway
        if not fed_gateway:
            raise HTTPException(
                status_code=500,
                detail=error_envelope(
                    code=ErrorCode.RESOURCE_UNAVAILABLE,
                    message=f"No gateway available for model: {model_id}",
                    source="master",
                    retryable=False,
                    data={"model_id": model_id},
                ),
            )

        try:
            result = await self._forward_embedding_request(
                gateway=fed_gateway,
                request_body=request_body,
                request_id=resolved_request_id,
            )
            from systems.proxy.core.lifecycle import emit_execution_completed

            await emit_execution_completed(
                event_bus=self.event_bus,
                url=fed_gateway.remote_stargate_url,
                model_id=model_id,
                request_id=resolved_request_id,
                gateway_id=fed_gateway.gateway_id,
            )
            return result
        except Exception:
            from systems.proxy.core.lifecycle import emit_execution_completed

            await emit_execution_completed(
                event_bus=self.event_bus,
                url=fed_gateway.remote_stargate_url if fed_gateway else "unknown",
                model_id=model_id,
                request_id=resolved_request_id,
                gateway_id=fed_gateway.gateway_id if fed_gateway else "unknown",
            )
            raise

    async def _forward_embedding_request(
        self,
        gateway: "FederatedGateway",
        request_body: dict,
        request_id: str,
    ) -> dict:
        """
        Forward embedding request to federated gateway.

        Args:
            gateway: Target gateway
            request_body: Embedding request
            request_id: Request identity (always provided by caller)

        Returns:
            Embedding response from gateway
        """
        # Validate required field (fail fast, no fallback)
        model_id = request_body.get("model")
        if model_id is None:
            raise HTTPException(
                status_code=400,
                detail=error_envelope(
                    code=ErrorCode.INVALID_REQUEST,
                    message="Missing required field: model",
                    source="master",
                    retryable=False,
                    data={"field": "model"},
                ),
            )

        # Get request_tracker from federation_integration
        request_tracker = None
        if self._federation_integration is not None:
            request_tracker = self._federation_integration.request_tracker

        if request_tracker is not None:
            return await request_tracker.forward_embedding(
                gateway=gateway,
                request_body=request_body,
                model_id=model_id,
                request_id=request_id,
            )
        else:
            # Fallback: direct forwarder (Edge/Remote mode, or no tracker configured)
            if self._federation_forwarder is None:
                raise HTTPException(
                    status_code=503,
                    detail=error_envelope(
                        code=ErrorCode.CONFIGURATION_ERROR,
                        message=(
                            "Federation forwarder not available (Master mode required)"
                        ),
                        source="master",
                        retryable=False,
                        data={},
                    ),
                )

            return await self._federation_forwarder.forward_embedding_request(
                gateway=gateway,
                request_body=request_body,
                request_id=request_id,
            )
