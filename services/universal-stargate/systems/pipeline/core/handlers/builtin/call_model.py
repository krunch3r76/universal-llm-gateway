"""Standalone model invocation implementation.

Extracted from BaseHandler._call_model so the logic is testable in isolation
and BaseHandler remains a thin orchestrator. All dependencies are passed
explicitly — no instance state, safe for concurrent execution.
"""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..protocol import PipelineContext
from .generation_params import ALLOWED_GENERATION_PARAMS, _build_generation_params
from .model_resolution import _resolve_model_alias_async
from .token_management import _check_context_feasibility
from .types import ModelCallResult

if TYPE_CHECKING:
    from ...schemas import StepConfig

logger = get_logger(__name__)


async def call_model(
    model_id: str,
    prompt: str,
    step: StepConfig,
    context: PipelineContext,
    system_prompt: str | None = None,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    json_schema: dict[str, Any] | None = None,
    disable_json_response: bool = False,
    call_label: str = "",
    metadata: dict[str, Any] | None = None,
    model_id_is_resolved: bool = False,
    model_profile: str | None = None,
    publish_event: Callable[[PipelineContext, Any], None] | None = None,
) -> ModelCallResult:
    """
    Invoke model and return complete result.

    Model aliases are automatically resolved via the pipeline registry.
    Use short aliases (e.g., "phi") or full IDs interchangeably.

    Emits a ModelInvocation event for every call (success or failure)
    so the pipeline viewer can display the full request/response chain.

    Args:
        model_id: Target model identifier (alias or full ID)
        prompt: User prompt content
        step: Step specification for timeout/retry options
        context: Pipeline context with dependencies
        system_prompt: Optional system prompt prepended to messages
        temperature: Generation temperature (None = model default)
        max_tokens: Max tokens (None = model default)
        json_schema: JSON schema for structured output
        disable_json_response: Remove response_format from params
        call_label: Purpose identifier for observability (e.g., "decompose",
            "verify", "classify"). Helps distinguish sub-calls in complex handlers.
        metadata: Optional dict forwarded to ModelInvocation for viewer linkage
            (e.g., claim_ids for verify_batch).
        model_id_is_resolved: If True, skip alias resolution — caller has
            already resolved the ID via registry (e.g. GenericGenerateHandler
            passes model_config.model directly to avoid a second round-trip).
            Pipeline-as-service IDs must be pre-resolved by the caller or
            resolved via _resolve_model_alias first; they are not re-entered here.
        model_profile: Profile name from the ModelRef definition (models.yaml).
            Sits between step-level and pipeline-level in the resolution hierarchy:
            step.profile > model_profile > pipeline.options.profile.
            When set and step.disable_profile is not explicitly True, also
            overrides the default disable_profile=True so the profile is applied.
        publish_event: Optional callback ``(context, event) → None`` for
            publishing bus-level events. Pass ``handler._publish_bus_event``.

    Returns:
        ModelCallResult with content, request body, tokens,
        and map_iteration_request_id.
        All data is per-call (no instance state mutation).

    Raises:
        ContextExceededError: If prompt exceeds model context (pre-flight)
        ProxyClientError: If model call fails or response cannot be parsed
    """
    from ...events.inference import ModelInvocation
    from ...execution.proxy_client import ProxyClientError

    # AUTO-RESOLVE model alias to full ID (unless caller already resolved it)
    resolved_model_id = (
        model_id
        if model_id_is_resolved
        else await _resolve_model_alias_async(model_id, context, step_name=step.name)
    )

    # Build messages
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    # Pre-flight: reject prompts that obviously exceed the model's context
    _check_context_feasibility(
        resolved_model_id,
        messages,
        step,
        context,
        system_prompt=system_prompt,
        user_prompt=prompt,
        publish_event=publish_event,
    )

    resolved_cfg = {
        "temperature": temperature,
        "max_tokens": max_tokens,
        "json_schema": json_schema,
    }
    params, removed_params = _build_generation_params(step, resolved_cfg)

    if removed_params and context.recorder:
        from ...events.inference import GenerationParamsFiltered

        context.recorder.emit(
            GenerationParamsFiltered(
                step_name=step.name,
                model_id=resolved_model_id,
                removed_keys=sorted(removed_params),
                allowed_keys=sorted(ALLOWED_GENERATION_PARAMS),
            )
        )

    # Explicitly remove response_format for free-form output (e.g., math LaTeX)
    if disable_json_response:
        params.pop("response_format", None)

    # Adapt response_format for target engine
    if "response_format" in params:
        from .....proxy.validation.response_format_converter import (
            convert_response_format_for_engine,
        )

        params["response_format"] = convert_response_format_for_engine(
            resolved_model_id, params["response_format"]
        )

    # Build complete request body (captured for debugging/viewer)
    request_body: dict[str, Any] = {
        "model": resolved_model_id,
        "messages": messages,
        "stream": False,
        **params,
    }

    # Determine HTTP timeout from step config
    # Extra 30s buffer for network latency and server processing above step timeout
    _http_timeout_buffer = 30
    http_timeout = None
    if step.handler_timeout_seconds:
        http_timeout = step.handler_timeout_seconds + _http_timeout_buffer
    elif step.timeout_seconds:
        http_timeout = step.timeout_seconds + _http_timeout_buffer

    # Resolve skip_token_counting: step overrides pipeline options
    skip_tc = step.skip_token_counting
    if skip_tc is None:
        skip_tc = context.pipeline.options.skip_token_counting

    # Resolve profile control.
    # Resolution order: step → model (ModelRef.profile) → pipeline options.
    # disable_profile defaults True in PipelineOptions — pipelines own their params.
    # Exception: when model_profile is set and the step doesn't explicitly
    # disable profiles, override the default so the model-level profile is applied.
    effective_disable_profile = step.disable_profile
    if effective_disable_profile is None:
        effective_disable_profile = context.pipeline.options.disable_profile
    effective_profile = step.profile
    if effective_profile is None:
        effective_profile = model_profile
    if effective_profile is None:
        effective_profile = context.pipeline.options.profile
    if model_profile and step.disable_profile is not True:
        effective_disable_profile = False

    recorder = context.recorder
    call_start = _time.monotonic()

    # Consume pre-generated request ID (if available) for the first call
    # of a map iteration — enables request.processing event correlation.
    # Subsequent calls within the same iteration get fresh UUIDs.
    inference_request_id = context.inference_request_id
    if inference_request_id:
        context.inference_request_id = None

    # Invoke via Stargate
    client = context.get_proxy_client()
    try:
        (
            response,
            map_iteration_request_id,
            snapshot_request_id,
        ) = await client.chat_completion(
            model=resolved_model_id,
            messages=messages,
            execution_id=context.execution_id,
            step_id=step.id,
            skip_token_counting=skip_tc,
            disable_profile=effective_disable_profile,
            profile=effective_profile,
            timeout=http_timeout,
            map_iteration_request_id=context.map_iteration_request_id,
            request_id=inference_request_id,
            **params,
        )
    except ProxyClientError as e:
        call_duration_ms = (_time.monotonic() - call_start) * 1000
        e.add_note(f"Pipeline step: {step.id}")
        e.add_note(f"Execution ID: {context.execution_id}")
        e.add_note(f"Model: {resolved_model_id}")
        if not model_id_is_resolved and resolved_model_id != model_id:
            e.add_note(f"Resolved from alias: {model_id}")
        logger.error(
            f"Model invocation failed: {e.status_code} {e.detail} "
            f"(step={step.id}, model={resolved_model_id})"
        )
        if recorder:
            recorder.emit(
                ModelInvocation(
                    step_name=step.name,
                    model_id=resolved_model_id,
                    call_label=call_label,
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    request_body=request_body,
                    error=f"{e.status_code} {e.detail}",
                    latency_ms=call_duration_ms,
                    success=False,
                    metadata=metadata,
                )
            )
        raise

    # Extract token usage
    usage = response.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    # Extract actual inference duration from llama.cpp timings.
    # predicted_ms = generation time only (excludes queue wait + prompt eval).
    # queue_wait = latency_ms - inference_ms gives the scheduling delay.
    timings = response.get("timings") or {}
    inference_ms = float(timings.get("predicted_ms", 0.0))

    # Extract content and finish_reason with validation
    try:
        choice = response["choices"][0]
        content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason", "unknown")
        if content is None:
            raise ProxyClientError(
                "Response content is None",
                status_code=502,
                detail=response,
            )
    except (KeyError, IndexError, TypeError) as e:
        raise ProxyClientError(
            f"Malformed response from Stargate: {e}",
            status_code=502,
            detail=response,
        ) from e

    # Fail fast on truncation — prevents corrupted output from
    # propagating to downstream steps (e.g., malformed JSON)
    if finish_reason == "length":
        from ...dag import ResponseTruncatedError

        effective_max_tokens = request_body.get("max_tokens")
        truncation_ms = (_time.monotonic() - call_start) * 1000
        if recorder:
            recorder.emit(
                ModelInvocation(
                    step_name=step.name,
                    model_id=resolved_model_id,
                    call_label=call_label,
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    request_body=request_body,
                    response_text=content,
                    error=(
                        "response_truncated: "
                        f"{completion_tokens} tokens, max={effective_max_tokens}"
                    ),
                    latency_ms=truncation_ms,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    success=False,
                    metadata=metadata,
                )
            )
        raise ResponseTruncatedError(
            step_id=step.id,
            completion_tokens=completion_tokens,
            max_tokens=effective_max_tokens,
            response_preview=content,
        )

    call_duration_ms = (_time.monotonic() - call_start) * 1000

    result = ModelCallResult(
        content=content,
        finish_reason=finish_reason,
        request_body=request_body,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        map_iteration_request_id=map_iteration_request_id,
        snapshot_request_id=snapshot_request_id,
        system_prompt=system_prompt,
        user_prompt=prompt,
    )

    # Emit observability event for every successful call
    if recorder:
        recorder.emit(
            ModelInvocation(
                step_name=step.name,
                model_id=resolved_model_id,
                call_label=call_label,
                snapshot_request_id=snapshot_request_id or "",
                system_prompt=system_prompt,
                user_prompt=prompt,
                request_body=request_body,
                response_text=content,
                latency_ms=call_duration_ms,
                inference_ms=inference_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                success=True,
                metadata=metadata,
            )
        )

    # Auto-record for pipeline-level token aggregation, keyed by step so
    # concurrent steps don't contaminate each other's call lists.
    context.record_model_call(result, step.name)

    return result
