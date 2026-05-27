"""Per-step model-call summary logging to the execution logger.

After ``record_success`` / ``record_failure`` drains the per-step model
calls, this module emits a single structured line to the
``systems.pipeline.execution`` logger summarising token totals, distinct
models touched, snapshot IDs, call count, and wall-clock duration. The
output format is consumed by external log-tailing pipelines and is
preserved character-for-character from the prior monolith.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .context import get_event_context

if TYPE_CHECKING:
    from .step_observability import StepObservability

execution_logger = get_logger("systems.pipeline.execution")


def log_step_model_calls(
    obs: StepObservability,
    step_name: str,
    calls: list[Any],
    duration: float,
    *,
    success: bool,
) -> None:
    """Log per-step model call summary to execution logger."""
    _, execution_id = get_event_context(obs)
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
