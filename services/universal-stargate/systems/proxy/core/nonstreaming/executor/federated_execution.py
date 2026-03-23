"""
Federated request dispatch: circuit-breaker check, body preparation, path selection.

Part of the `nonstreaming/executor` subpackage. `execute_federated_request` is
the sole public entry point; it validates the gateway selection, applies the
circuit breaker, optionally transforms the request body for prompt-schema models,
then delegates to `federated_streaming` or `federated_nonstreaming`.

Streaming/non-streaming implementations live in their own modules to keep
each file within the 300 SLOC target:
- `federated_streaming.py`    — SSE stream forwarding
- `federated_nonstreaming.py` — JSON response forwarding + tracker/forwarder dispatch
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.responses import Response
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope

from src.scheduling.events import (
    federated_request_prompt_transformation_applied,
    federated_request_prompt_transformation_failed,
    federated_request_prompt_transformation_skipped,
)

from ...endpoint_category import derive_endpoint_category
from .token_capping import _cap_max_tokens_to_slot_context

if TYPE_CHECKING:
    from ..context import RequestContext

logger = get_logger(__name__)


async def execute_federated_request(
    context: RequestContext,
    federation_forwarder: Any,
    federation_circuit_breaker: Any,
    transformation_engine: Any,
    federation_integration: Any,
    event_bus: Any,
    release_capacity_token: Any,
    federated_manager: Any | None = None,
) -> Response:
    """
    Dispatch a federated request to streaming or non-streaming execution.

    Validates the gateway selection, checks the circuit breaker, builds the
    request body (including prompt transformation for prompt-schema models),
    then delegates to `_execute_federated_streaming` or
    `_execute_federated_nonstreaming`.

    Args:
        context: Request context with gateway selection and model ID.
        federation_forwarder: Direct HTTP forwarder for Edge/Remote fallback.
        federation_circuit_breaker: Optional circuit breaker for gateway health.
        transformation_engine: Optional engine for messages→prompt conversion.
        federation_integration: Provides `.request_tracker` in Master mode.
        event_bus: Event bus for observability signals.
        release_capacity_token: Async callable `(context)` that releases the
            capacity slot held for the request.

    Returns:
        FastAPI Response (JSONResponse or TrackedStreamingResponse).

    Raises:
        HTTPException: 500 if gateway unset, 503 if forwarder missing,
            502 if circuit open, or propagated from the execution path.

    INVARIANT:
        ∀ federated_request:
            circuit_allows(gateway) ⟹ forward
            ∧ (¬streaming ∧ success) ⟹ record_success
            ∧ (¬streaming ∧ failure) ⟹ record_failure
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

    if federation_forwarder is None:
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

    if federation_circuit_breaker:
        if not await federation_circuit_breaker.should_allow_request(
            fed_gateway.gateway_id,
            str(context.selected_model),
        ):
            raise HTTPException(
                status_code=502,
                detail=error_envelope(
                    code=ErrorCode.RESOURCE_UNAVAILABLE,
                    message=f"Federated gateway {fed_gateway.gateway_id} circuit open",
                    source="master",
                    retryable=True,
                    data={"gateway_id": fed_gateway.gateway_id},
                ),
            )

    # context is per-request and not reused; modified_request is a one-time overlay
    request_body = context.original_request.copy()
    if context.modified_request:
        request_body.update(context.modified_request)

    # Safety cap: ensure max_tokens ≤ effective per-slot context.
    # Pipeline requests skip token counting, so this is the backstop.
    _cap_max_tokens_to_slot_context(request_body, fed_gateway, context.selected_model)

    from ....debug.request_snapshots import write_request_snapshot

    await write_request_snapshot(request_body, context.request_id, stage="after")

    request_id = context.request_id
    hop_count = 1

    # CRITICAL: Use endpoint category from routing — re-deriving causes capacity leaks.
    endpoint_category = context.routing_endpoint_category
    if endpoint_category is None:
        logger.warning(
            "⚠️ routing_endpoint_category not set, deriving from request path"
        )
        endpoint_category = derive_endpoint_category(request=context.http_request)

    model_id = context.selected_model
    input_schema = (
        fed_gateway.model_resources.get(model_id, {}).get("input_schema", "messages")
        if fed_gateway.model_resources
        else "messages"
    )

    hints: dict[str, Any] = {"input_schema": input_schema}
    if context.request_timeout_hint:
        hints["timeout"] = context.request_timeout_hint

    if not transformation_engine or input_schema != "prompt":
        reason = "no_engine" if not transformation_engine else "schema_not_prompt"
        if event_bus:
            event_bus.publish_async_nowait(
                federated_request_prompt_transformation_skipped(
                    request_id=request_id,
                    model_id=model_id.routing_key,
                    gateway_id=fed_gateway.gateway_id,
                    reason=reason,
                )
            )
    else:
        original_body = request_body
        request_body = _apply_prompt_transformation(
            transformation_engine, request_body, model_id
        )
        if request_body is not original_body:
            prompt = request_body.get("prompt", "")
            prompt_chars = len(prompt) if isinstance(prompt, str) else 0
            if event_bus:
                event_bus.publish_async_nowait(
                    federated_request_prompt_transformation_applied(
                        request_id=request_id,
                        model_id=model_id.routing_key,
                        gateway_id=fed_gateway.gateway_id,
                        prompt_chars=prompt_chars,
                    )
                )
        else:
            if event_bus:
                event_bus.publish_async_nowait(
                    federated_request_prompt_transformation_failed(
                        request_id=request_id,
                        model_id=model_id.routing_key,
                        gateway_id=fed_gateway.gateway_id,
                        error=(
                            "transformation returned original body "
                            "(see logger.exception above)"
                        ),
                    )
                )

    logger.info(f"📤 REQUEST BODY (to Federated Gateway): {json.dumps(request_body)}")
    logger.info(
        f"🌐 Forwarding to federated gateway {fed_gateway.gateway_id} "
        f"via {fed_gateway.remote_stargate_id} "
        f"(request={request_id[:8]}, hints={hints})"
    )

    if context.client_wants_streaming:
        from .federated_streaming import _execute_federated_streaming

        return await _execute_federated_streaming(
            context,
            fed_gateway,
            request_body,
            request_id,
            hop_count,
            endpoint_category,
            hints,
            federation_integration,
            federation_forwarder,
            release_capacity_token,
        )

    from .federated_nonstreaming import _execute_federated_nonstreaming

    try:
        response = await _execute_federated_nonstreaming(
            context,
            fed_gateway,
            request_body,
            request_id,
            hop_count,
            endpoint_category,
            hints,
            federation_integration,
            federation_forwarder,
            event_bus,
            federated_manager=federated_manager,
        )

        if federation_circuit_breaker:
            await federation_circuit_breaker.record_success(
                fed_gateway.gateway_id,
                str(context.selected_model),
            )

        return response

    except HTTPException as e:
        if federation_circuit_breaker:
            # Cloud 4xx errors are preserved with their original status code
            # by raise_federated_http_error(), so they fall below this >= 500
            # threshold and don't trip the breaker. Cloud 5xx and all
            # local/federated errors are wrapped as 502, correctly tripping.
            #
            # 504 = Gateway Timeout: the gateway is slow (under load), not broken.
            # Opening the circuit for timeouts starves healthy-but-busy gateways —
            # all subsequent requests pile on the remaining gateway, causing cascading
            # saturation. Retry exclusion (context.excluded_gateway_ids) already
            # prevents re-sending the same timed-out request to the same gateway.
            if e.status_code >= 500 and e.status_code != 504:
                await federation_circuit_breaker.record_failure(
                    fed_gateway.gateway_id,
                    str(context.selected_model),
                    error=str(e.detail),
                )
        raise
    except Exception as e:
        if federation_circuit_breaker:
            await federation_circuit_breaker.record_failure(
                fed_gateway.gateway_id,
                str(context.selected_model),
                error=str(e),
            )
        raise


def _apply_prompt_transformation(
    transformation_engine: Any,
    request_body: dict[str, Any],
    model_id: Any,
) -> dict[str, Any]:
    """
    Convert a messages-based request body to prompt format.

    Mutates a copy of the dict (never the original). On failure, logs at
    ERROR level and returns the original body to let the Edge/Gateway handle
    the mismatch (rather than silently swallowing the error or aborting).

    Args:
        transformation_engine: Engine with a
            `transform(messages, model, target_format)` method.
        request_body: Original request dict containing `messages`.
        model_id: Parsed ModelId used for transformation routing.

    Returns:
        Transformed request body (messages replaced with prompt), or the
        unmodified original if transformation fails.
    """
    from systems.transformations import OutputFormat

    messages = request_body.get("messages")
    if not messages:
        return request_body

    try:
        result = transformation_engine.transform(
            messages=messages,
            model=model_id,
            target_format=OutputFormat.PROMPT,
        )
        transformed = request_body.copy()
        del transformed["messages"]
        transformed["prompt"] = result.content
        logger.info(
            f"🔄 Master applied transformation for {model_id} "
            f"(input_schema=prompt): {len(str(result.content))} chars"
        )
        return transformed
    except Exception as e:
        logger.exception(
            f"❌ Master transformation failed for {model_id}: {e}. "
            "Forwarding original request (may fail at Edge/Gateway)."
        )
        return request_body
