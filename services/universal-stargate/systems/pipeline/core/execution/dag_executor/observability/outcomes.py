"""Step terminal-state recording: success and failure paths.

Both functions sit at the boundary between executor state mutation and
observability emission. They:

- drain the per-step model-call ledger via ``context.drain_step_calls``
- aggregate prompt/completion tokens onto the ``StepOutput`` when the
  caller has not pre-populated them (success path) or onto a synthetic
  rollup for failure-path bus events (failure path)
- emit the per-step model-call log line via ``log_step_model_calls``
- transition node state (``COMPLETED`` / ``FAILED``), clear per-step
  progress, set executor output (success only, non-map steps), append
  execution order, and propagate completion downstream
- emit recorder lifecycle events (``StepOutputCaptured`` +
  ``StepCompleted`` on success; ``StepFailed`` on failure) and the
  corresponding bus events

Ordering of state mutations and event emissions is preserved verbatim
from the prior monolith — downstream consumers (recorders, schedulers,
debug-file writers) depend on this sequence.

``record_failure`` additionally enriches ``StepTimeoutError`` with token
totals and progress metadata so timeout reports can surface partial work,
and writes a failure-debug artifact via ``pipeline_failure_debug`` inside
a try/except so a debug-write failure cannot mask the original step
error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from ....dag import StepState
from ....events.lifecycle import (
    StepCompleted,
    StepFailed,
    StepOutputCaptured,
)
from ....events.step import StepCompleted as BusStepCompleted
from ....events.step import StepFailed as BusStepFailed
from ...errors import StepTimeoutError
from .context import get_event_context, publish_event
from .model_call_logging import log_step_model_calls

if TYPE_CHECKING:
    from ....dag import StepNode
    from ....handlers.protocol import StepOutput
    from .step_observability import StepObservability

logger = get_logger(__name__)


def record_success(
    obs: StepObservability,
    node: StepNode,
    output: StepOutput,
    duration: float,
) -> None:
    """Record successful step completion with auto-aggregated tokens."""
    step_calls = obs._executor.context.drain_step_calls(node.step.name)
    if step_calls:
        output.model_call_count = len(step_calls)
        if output.prompt_tokens == 0 and output.completion_tokens == 0:
            output.prompt_tokens = sum(c.prompt_tokens for c in step_calls)
            output.completion_tokens = sum(c.completion_tokens for c in step_calls)
            logger.debug(
                "Step '%s': auto-aggregated %d tokens from %d model calls",
                node.step.name,
                output.prompt_tokens + output.completion_tokens,
                len(step_calls),
            )
        log_step_model_calls(obs, node.step.name, step_calls, duration, success=True)

    node.output = output
    node.state = StepState.COMPLETED
    progress_by_step = getattr(obs._executor.context, "_step_progress_by_step", {})
    if node.step.name in progress_by_step:
        progress_by_step.pop(node.step.name, None)
        setattr(obs._executor.context, "_step_progress_by_step", progress_by_step)

    if not node.step.is_map_step:
        obs._executor.context.set_output(node.step.name, output)
    obs._executor.execution_order.append(node.step.name)
    latency_ms = output.latency_ms
    logger.info(f"Step '{node.step.name}' completed (latency: {latency_ms:.0f}ms)")
    obs._executor._propagate_completion(node.step.name)

    recorder = obs._executor.context.recorder
    if recorder:
        recorder.emit(
            StepOutputCaptured(
                step_name=node.step.name,
                model_id=output.model_id,
                raw=output.raw,
                json_data=output.json,
                json_parse_error=getattr(output, "json_parse_error", None),
                prompt_tokens=output.prompt_tokens,
                completion_tokens=output.completion_tokens,
                latency_ms=output.latency_ms,
                model_call_count=getattr(output, "model_call_count", 0),
                system_prompt=output.system_prompt,
                user_prompt=output.user_prompt,
                request_body=output.request_body,
            )
        )
        recorder.emit(
            StepCompleted(
                step_name=node.step.name,
                model_id=output.model_id,
                duration_ms=duration * 1000,
                prompt_tokens=output.prompt_tokens,
                completion_tokens=output.completion_tokens,
                model_call_count=getattr(output, "model_call_count", 0),
            )
        )

    pipeline_id, execution_id = get_event_context(obs)
    output_length = len(output.text) if hasattr(output, "text") else 0
    exit_code: int | None = None
    if output.json and isinstance(output.json.get("exit_code"), int):
        exit_code = output.json["exit_code"]
    step_completed_kwargs: dict[str, object] = {
        "pipeline_id": pipeline_id,
        "execution_id": execution_id,
        "step_name": node.step.name,
        "duration_seconds": duration,
        "output_length": output_length,
        "prompt_tokens": output.prompt_tokens,
        "completion_tokens": output.completion_tokens,
        "model_call_count": getattr(output, "model_call_count", 0),
        "model_id": output.model_id,
        "exit_code": exit_code,
        "json_output_keys": (list(output.json.keys()) if output.json else None),
    }
    cached_tokens = getattr(output, "cached_tokens", None)
    if cached_tokens is not None:
        step_completed_kwargs["cached_tokens"] = cached_tokens
    publish_event(obs, BusStepCompleted(**step_completed_kwargs))


def record_failure(
    obs: StepObservability, node: StepNode, error: Exception, duration: float
) -> None:
    """Record step failure, preserving timeout/debug metadata semantics."""
    all_calls = obs._executor.context.drain_step_calls(node.step.name)
    successful_calls = [
        call for call in all_calls if getattr(call, "success", True) is True
    ]
    total_prompt = sum(getattr(call, "prompt_tokens", 0) for call in successful_calls)
    total_completion = sum(
        getattr(call, "completion_tokens", 0) for call in successful_calls
    )
    if all_calls:
        log_step_model_calls(obs, node.step.name, all_calls, duration, success=False)

    node.state = StepState.FAILED
    node.error = error

    pipeline_id, execution_id = get_event_context(obs)
    call_contexts = None
    if all_calls:
        call_contexts = [
            {
                "request_id": getattr(c, "snapshot_request_id", None),
                "request_body": getattr(c, "request_body", {}),
                "response_content": getattr(c, "content", None),
                "model": (
                    getattr(c, "request_body", {}).get("model")
                    if isinstance(getattr(c, "request_body", None), dict)
                    else None
                ),
                "prompt_tokens": getattr(c, "prompt_tokens", 0),
                "completion_tokens": getattr(c, "completion_tokens", 0),
                "success": getattr(c, "success", True),
            }
            for c in all_calls
        ]
    progress_by_step = getattr(obs._executor.context, "_step_progress_by_step", {})
    step_progress = progress_by_step.pop(node.step.name, None)
    setattr(obs._executor.context, "_step_progress_by_step", progress_by_step)
    if isinstance(error, StepTimeoutError):
        error.prompt_tokens = total_prompt
        error.completion_tokens = total_completion
        error.model_call_count = len(all_calls)
        if isinstance(step_progress, dict):
            error.items_total = step_progress.get("items_total")
            error.items_completed = step_progress.get("items_completed")
    try:
        from ....pipeline_failure_debug import write_failure_debug

        write_failure_debug(
            pipeline_id=pipeline_id or "",
            execution_id=execution_id or "",
            step_id=node.step.name,
            error=error,
            call_contexts=call_contexts,
        )
    except Exception as e:
        logger.warning("Could not write failure debug file: %s", e)

    import traceback as tb_mod

    tb_str = "".join(tb_mod.format_exception(type(error), error, error.__traceback__))

    recorder = obs._executor.context.recorder
    if recorder:
        recorder.emit(
            StepFailed(
                step_name=node.step.name,
                error=str(error),
                duration_ms=duration * 1000,
                traceback=tb_str,
                model_calls=call_contexts,
                prompt_tokens=total_prompt,
                completion_tokens=total_completion,
                model_call_count=len(all_calls),
            )
        )
    publish_event(
        obs,
        BusStepFailed(
            pipeline_id=pipeline_id,
            execution_id=execution_id,
            step_name=node.step.name,
            duration_seconds=duration,
            error=str(error),
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            model_call_count=len(all_calls),
            traceback=tb_str,
        ),
    )
