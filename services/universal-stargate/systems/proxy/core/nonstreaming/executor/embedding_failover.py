"""CPU embedding failover helpers for execute_embedding_request."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from universal_protocol import ErrorCode, error_envelope

if TYPE_CHECKING:
    from systems.proxy.core.nonstreaming.context import RequestContext

CPU_EMBEDDING_MODEL_ID = "qwen3-embedding-8b-q8-0-8192-cpu"

_IMMEDIATE_FAILOVER_CODES = frozenset(
    {
        ErrorCode.NO_FEASIBLE_GATEWAY,
        ErrorCode.INSUFFICIENT_VRAM,
    }
)


def http_exception_triggers_cpu_failover(exc: HTTPException) -> bool:
    """Immediate GPU-path failures that may fail over to CPU (not capacity wait)."""
    if exc.status_code != 503:
        return False
    detail = exc.detail
    if not isinstance(detail, dict):
        return False
    code = detail.get("code")
    if isinstance(code, ErrorCode):
        return code in _IMMEDIATE_FAILOVER_CODES
    return code in {c.value for c in _IMMEDIATE_FAILOVER_CODES}


def capacity_budget_expired(context: RequestContext) -> bool:
    deadline = getattr(context, "_capacity_deadline_mono", None)
    return deadline is not None and time.monotonic() >= deadline


def should_attempt_cpu_failover(exc: HTTPException, context: RequestContext) -> bool:
    """Decide whether to retry on the CPU embedding model.

    Immediate triggers: NO_FEASIBLE_GATEWAY, INSUFFICIENT_VRAM.
    GATEWAY_AT_CAPACITY only after the embedding capacity deadline expires.
    We do not fail over to CPU solely because the overall deadline expired on
    other errors — GPU had the bounded window; CPU is not an unbounded fallback.
    """
    if http_exception_triggers_cpu_failover(exc):
        return True
    if not capacity_budget_expired(context):
        return False
    if exc.status_code != 503:
        return False
    detail = exc.detail
    if not isinstance(detail, dict):
        return False
    code = detail.get("code")
    if isinstance(code, ErrorCode):
        return code == ErrorCode.GATEWAY_AT_CAPACITY
    return code == ErrorCode.GATEWAY_AT_CAPACITY.value


async def run_embedding_with_cpu_failover(
    *,
    context: RequestContext,
    parsed_model_id: Any,
    request_body: dict[str, Any],
    resolved_request_id: str,
    select_gateway_fn: Callable[[RequestContext], Awaitable[None]],
    forward_with_retry_fn: Callable[..., Awaitable[dict[str, Any]]],
    forward_embedding_fn: Callable[..., Awaitable[dict[str, Any]]],
    release_capacity_token_fn: Callable[[RequestContext], Awaitable[None]],
    emit_execution_completed_fn: Callable[..., Awaitable[None]],
    event_bus: Any,
    oom_recovery_fn: Callable[..., Awaitable[bool]] | None,
    cpu_failover_enabled: bool,
) -> dict[str, Any]:
    from model_id import ModelId

    attempt_models = [parsed_model_id]
    if cpu_failover_enabled:
        attempt_models.append(ModelId.parse(CPU_EMBEDDING_MODEL_ID))

    fed_gateway = None
    last_exc: Exception | None = None
    for attempt_idx, active_model in enumerate(attempt_models):
        if attempt_idx > 0 and not cpu_failover_enabled:
            break
        context.selected_model = active_model
        active_body = {**request_body, "model": str(active_model)}
        try:
            await select_gateway_fn(context)
        except HTTPException as sel_exc:
            last_exc = sel_exc
            if (
                attempt_idx == 0
                and cpu_failover_enabled
                and should_attempt_cpu_failover(sel_exc, context)
            ):
                continue
            raise

        fed_gateway = context.federated_gateway
        if not fed_gateway:
            no_gw = HTTPException(
                status_code=503,
                detail=error_envelope(
                    code=ErrorCode.RESOURCE_UNAVAILABLE,
                    message=f"No gateway available for model: {active_model}",
                    source="master",
                    retryable=True,
                    data={"model_id": str(active_model)},
                ),
            )
            last_exc = no_gw
            if attempt_idx == 0 and cpu_failover_enabled:
                await release_capacity_token_fn(context)
                continue
            raise no_gw

        try:
            result = await forward_with_retry_fn(
                forward_fn=forward_embedding_fn,
                fed_gateway=fed_gateway,
                request_body=active_body,
                request_id=resolved_request_id,
                parsed_model_id=active_model,
                oom_recovery_fn=oom_recovery_fn,
            )
        except HTTPException as fwd_exc:
            last_exc = fwd_exc
            if (
                attempt_idx == 0
                and cpu_failover_enabled
                and should_attempt_cpu_failover(fwd_exc, context)
            ):
                await release_capacity_token_fn(context)
                continue
            raise

        await emit_execution_completed_fn(
            event_bus=event_bus,
            url=fed_gateway.remote_stargate_url,
            model_id=context.selected_model.routing_key,
            request_id=resolved_request_id,
            gateway_id=fed_gateway.gateway_id,
        )
        return result

    if last_exc is not None:
        raise last_exc
    raise HTTPException(status_code=503, detail="Embedding routing failed")
