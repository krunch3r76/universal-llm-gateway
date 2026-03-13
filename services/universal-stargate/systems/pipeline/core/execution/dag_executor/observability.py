"""Observability helpers for DAG executor step lifecycle."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from universal_event_bus import Event
from universal_logging import get_logger

from ...dag import StepState
from ...events.lifecycle import (
    StepCompleted,
    StepConditionEvaluated,
    StepFailed,
    StepInputsCaptured,
    StepOutputCaptured,
    StepSkipped,
    StepStarted,
)
from ...events.step import StepCompleted as BusStepCompleted
from ...events.step import StepConditionEvaluated as BusStepConditionEvaluated
from ...events.step import StepFailed as BusStepFailed
from ...events.step import StepSkipped as BusStepSkipped
from ...events.step import StepStarted as BusStepStarted
from ..errors import StepTimeoutError

if TYPE_CHECKING:
    from ...dag import StepNode
    from ...handlers.protocol import StepOutput
    from ...schemas import StepConfig
    from .executor import DAGExecutor

logger = get_logger(__name__)
execution_logger = get_logger("systems.pipeline.execution")


class StepObservability:
    """Owns DAG executor observability and event emission behavior."""

    def __init__(self, executor: DAGExecutor) -> None:
        self._executor = executor
        self._event_bus_warned = False

    def get_event_context(self) -> tuple[str, str]:
        """Extract pipeline_id and execution_id from context."""
        pipeline = getattr(self._executor.context, "pipeline", None)
        if pipeline is None:
            logger.error("Missing context.pipeline - using 'unknown' for events")
            pipeline_id = "unknown"
        else:
            pipeline_id = pipeline.id

        execution_id = getattr(self._executor.context, "execution_id", None)
        if execution_id is None:
            logger.error("Missing context.execution_id - using 'unknown' for events")
            execution_id = "unknown"

        return pipeline_id, execution_id

    def publish_event(self, event: Event) -> None:
        """
        Publish event via context's event bus (fire-and-forget).

        Logs WARN once if event_bus unavailable.
        """
        proxy = getattr(self._executor.context, "_proxy", None)
        event_bus = getattr(proxy, "event_bus", None) if proxy else None
        if event_bus:
            asyncio.create_task(event_bus.publish_async_nowait(event))
        elif not self._event_bus_warned:
            logger.warning("Event bus unavailable - events will not be published")
            self._event_bus_warned = True

    def emit_condition_evaluated(
        self,
        *,
        node: StepNode,
        condition_expr: str,
        should_execute: bool,
        available_outputs: list[str],
    ) -> None:
        """Emit condition evaluation event to recorder and legacy bus."""
        recorder = self._executor.context.recorder
        if recorder:
            recorder.emit(
                StepConditionEvaluated(
                    step_name=node.step.name,
                    condition=condition_expr,
                    result=should_execute,
                    available_outputs=available_outputs,
                )
            )
        pipeline_id, execution_id = self.get_event_context()
        self.publish_event(
            BusStepConditionEvaluated(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=node.step.name,
                condition=condition_expr,
                result=should_execute,
                available_outputs=available_outputs,
            )
        )

    def emit_step_skipped(self, *, node: StepNode, reason: str) -> None:
        """Emit skip event to recorder and legacy bus."""
        recorder = self._executor.context.recorder
        if recorder:
            recorder.emit(
                StepSkipped(
                    step_name=node.step.name,
                    reason=reason,
                )
            )
        pipeline_id, execution_id = self.get_event_context()
        self.publish_event(
            BusStepSkipped(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=node.step.name,
                reason=reason,
            )
        )

    def emit_step_started(self, *, node: StepNode, target_model: str | None) -> None:
        """Emit step started events to recorder and legacy bus."""
        recorder = self._executor.context.recorder
        if recorder:
            recorder.emit(
                StepStarted(
                    step_name=node.step.name,
                    step_type=node.step.type,
                    model_id=target_model,
                    is_map_step=node.step.is_map_step,
                )
            )
        pipeline_id, execution_id = self.get_event_context()
        self.publish_event(
            BusStepStarted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=node.step.name,
                step_type=node.step.type,
                model_id=target_model,
                is_map_step=node.step.is_map_step,
            )
        )

    def emit_step_inputs(self, *, node: StepNode) -> None:
        """Capture and emit step inputs for recorder observability."""
        recorder = self._executor.context.recorder
        if not recorder:
            return
        inputs = self.capture_step_inputs(node.step)
        if inputs:
            recorder.emit(StepInputsCaptured(step_name=node.step.name, inputs=inputs))

    def record_success(
        self, node: StepNode, output: StepOutput, duration: float
    ) -> None:
        """Record successful step completion with auto-aggregated tokens."""
        step_calls = self._executor.context.drain_step_calls(node.step.name)
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
            self.log_step_model_calls(
                node.step.name, step_calls, duration, success=True
            )

        node.output = output
        node.state = StepState.COMPLETED
        progress_by_step = getattr(self._executor.context, "_step_progress_by_step", {})
        if node.step.name in progress_by_step:
            progress_by_step.pop(node.step.name, None)
            setattr(self._executor.context, "_step_progress_by_step", progress_by_step)

        if not node.step.is_map_step:
            self._executor.context.set_output(node.step.name, output)
        self._executor.execution_order.append(node.step.name)
        latency_ms = output.latency_ms
        logger.info(f"Step '{node.step.name}' completed (latency: {latency_ms:.0f}ms)")
        self._executor._propagate_completion(node.step.name)

        recorder = self._executor.context.recorder
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

        pipeline_id, execution_id = self.get_event_context()
        output_length = len(output.text) if hasattr(output, "text") else 0
        exit_code: int | None = None
        if output.json and isinstance(output.json.get("exit_code"), int):
            exit_code = output.json["exit_code"]
        self.publish_event(
            BusStepCompleted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=node.step.name,
                duration_seconds=duration,
                output_length=output_length,
                prompt_tokens=output.prompt_tokens,
                completion_tokens=output.completion_tokens,
                model_call_count=getattr(output, "model_call_count", 0),
                model_id=output.model_id,
                exit_code=exit_code,
                json_output_keys=(list(output.json.keys()) if output.json else None),
            )
        )

    def record_failure(self, node: StepNode, error: Exception, duration: float) -> None:
        """Record step failure, preserving timeout/debug metadata semantics."""
        all_calls = self._executor.context.drain_step_calls(node.step.name)
        successful_calls = [
            call for call in all_calls if getattr(call, "success", True) is True
        ]
        total_prompt = sum(
            getattr(call, "prompt_tokens", 0) for call in successful_calls
        )
        total_completion = sum(
            getattr(call, "completion_tokens", 0) for call in successful_calls
        )
        if all_calls:
            self.log_step_model_calls(
                node.step.name, all_calls, duration, success=False
            )

        node.state = StepState.FAILED
        node.error = error

        pipeline_id, execution_id = self.get_event_context()
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
        progress_by_step = getattr(self._executor.context, "_step_progress_by_step", {})
        step_progress = progress_by_step.pop(node.step.name, None)
        setattr(self._executor.context, "_step_progress_by_step", progress_by_step)
        if isinstance(error, StepTimeoutError):
            error.prompt_tokens = total_prompt
            error.completion_tokens = total_completion
            error.model_call_count = len(all_calls)
            if isinstance(step_progress, dict):
                error.items_total = step_progress.get("items_total")
                error.items_completed = step_progress.get("items_completed")
        try:
            from ...pipeline_failure_debug import write_failure_debug

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

        tb_str = "".join(
            tb_mod.format_exception(type(error), error, error.__traceback__)
        )

        recorder = self._executor.context.recorder
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
        self.publish_event(
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
            )
        )

    def capture_step_inputs(self, step: StepConfig) -> dict[str, Any]:
        """Capture resolved handler inputs for observability."""
        from ..resolver import NamespaceResolver, traverse_path

        inputs: dict[str, Any] = {}
        if not step.handler_inputs:
            return inputs
        resolver = NamespaceResolver(self._executor.context)
        for input_name, binding in step.handler_inputs.items():
            try:
                root = resolver.resolve(binding)
                value = (
                    traverse_path(
                        root,
                        binding.field_path,
                        step_name=step.id,
                        field_name=input_name,
                        binding_repr=str(binding),
                        resolver=resolver,
                    )
                    if binding.field_path
                    else root
                )
                if isinstance(value, str) and len(value) > 2000:
                    value = value[:2000] + f"... ({len(value)} chars total)"
                inputs[input_name] = {
                    "source": str(binding),
                    "value": value,
                }
            except Exception as e:
                logger.warning(
                    "Failed to capture input '%s' for step '%s' (binding=%s): %s",
                    input_name,
                    step.id,
                    binding,
                    e,
                )
                inputs[input_name] = {"source": str(binding), "value": None}
        return inputs

    def log_step_model_calls(
        self,
        step_name: str,
        calls: list[Any],
        duration: float,
        *,
        success: bool,
    ) -> None:
        """Log per-step model call summary to execution logger."""
        _, execution_id = self.get_event_context()
        total_prompt = sum(c.prompt_tokens for c in calls)
        total_completion = sum(c.completion_tokens for c in calls)
        total_tokens = total_prompt + total_completion

        models: list[str] = []
        snapshot_ids: list[str] = []
        for call in calls:
            model = call.request_body.get("model", "unknown")
            if model not in models:
                models.append(model)
            snap_id = getattr(call, "snapshot_request_id", None)
            if snap_id:
                snapshot_ids.append(snap_id)

        status = "completed" if success else "failed"
        model_str = ", ".join(models)
        snap_str = ", ".join(snapshot_ids) if snapshot_ids else "none"

        execution_logger.info(
            f"Step '{step_name}' {status}: "
            f"execution_id={execution_id}, "
            f"model=[{model_str}], calls={len(calls)}, "
            f"prompt_tokens={total_prompt}, "
            f"completion_tokens={total_completion}, "
            f"total_tokens={total_tokens}, "
            f"duration={duration:.2f}s, "
            f"snapshot_ids=[{snap_str}]"
        )
