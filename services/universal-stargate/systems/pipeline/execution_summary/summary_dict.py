"""
Structured summary-dict builders for pipeline execution summaries.

Provides the canonical dict shape written to disk as ``summary.json`` and
serialized in YAML form, plus the filename-generation helper. Single source
of truth for the on-disk summary schema — previously inlined in three places
(``write_summary``, ``write_summary_yaml``, ``write_step_summaries``); now
funneled through ``build_summary_dict``.

Pure builders: no I/O. Side-effect-free transforms over pipeline spec +
execution context.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.handlers.protocol import PipelineContext
    from ..core.schemas import PipelineSpec


def build_execution_details(
    context: PipelineContext,
    pipeline: PipelineSpec,
    execution_order: list[str] | None,
) -> dict[str, Any]:
    """
    Build detailed per-step execution information.

    Walks the ordered steps and produces a dict with ``step_count``, a
    ``steps`` list (each step's id/type/model/latency/tokens/prompts/json/
    config), and the ``final_output_step`` id (when the pipeline declares one).

    Args:
        context: Pipeline execution context.
        pipeline: Pipeline specification (for step-spec lookup).
        execution_order: Optional list of step IDs in execution order; falls
            back to ``context.outputs`` insertion order.

    Returns:
        Execution-details dict suitable as the ``execution`` field of the
        on-disk summary.
    """
    step_specs = {step.id: step for step in pipeline.steps}

    if execution_order:
        ordered_steps = execution_order
    else:
        ordered_steps = list(context.outputs.keys())

    steps_detail = []
    for step_id in ordered_steps:
        output = context.outputs.get(step_id)
        spec = step_specs.get(step_id)

        if output is None:
            continue

        step_info: dict[str, Any] = {
            "step_id": step_id,
            "step_type": spec.type if spec else "unknown",
            "model_id": output.model_id,
            "latency_ms": round(output.latency_ms, 2) if output.latency_ms else 0,
            "raw_output": output.raw,
            "extracted_text": output.text,
            "tokens": {
                "prompt_tokens": output.prompt_tokens,
                "completion_tokens": output.completion_tokens,
                "total_tokens": output.prompt_tokens + output.completion_tokens,
            },
        }

        if output.system_prompt or output.user_prompt:
            step_info["prompts"] = {}
            if output.system_prompt:
                step_info["prompts"]["system"] = output.system_prompt
            if output.user_prompt:
                step_info["prompts"]["user"] = output.user_prompt

        if output.request_body:
            step_info["request_body"] = output.request_body

        if output.json:
            step_info["json"] = output.json

        if spec:
            step_info["config"] = {
                "model_ref": getattr(spec, "model_ref", None),
                "prompt_ref": getattr(spec, "prompt_ref", None),
                "temperature": output.temperature,
                "max_tokens": output.max_tokens,
                "depends_on": getattr(spec, "depends_on", []),
            }

        steps_detail.append(step_info)

    return {
        "step_count": len(steps_detail),
        "steps": steps_detail,
        "final_output_step": pipeline.output if hasattr(pipeline, "output") else None,
    }


def build_summary_dict(
    pipeline: PipelineSpec,
    context: PipelineContext,
    request_body: dict[str, Any],
    response_body: dict[str, Any],
    execution_order: list[str] | None,
    timestamp: datetime,
) -> dict[str, Any]:
    """
    Build the canonical summary dict written as ``summary.json`` / YAML.

    Single source of truth for the on-disk summary schema. All three callers
    (``write_summary`` JSON path, ``write_summary_yaml``, and the
    ``summary.json`` inside ``write_step_summaries`` exec dirs) funnel through
    this builder.

    Args:
        pipeline: Pipeline specification.
        context: Pipeline execution context.
        request_body: Original request body.
        response_body: Final response body.
        execution_order: Optional list of step IDs in execution order.
        timestamp: Timestamp to embed in metadata.

    Returns:
        Dict shaped ``{metadata, request, execution, response, options}``.
    """
    return {
        "metadata": {
            "pipeline_id": pipeline.id,
            "pipeline_version": pipeline.version,
            "pipeline_type": pipeline.domain,
            "execution_id": context.execution_id,
            "timestamp": timestamp.isoformat(),
            "execution_time_ms": (
                (datetime.now() - context.started_at).total_seconds() * 1000
            ),
        },
        "request": {
            "source_text": context.source_text,
            "full_request": request_body,
        },
        "execution": build_execution_details(context, pipeline, execution_order),
        "response": response_body,
        "options": pipeline.options.model_dump(),
    }


def generate_summary_filename(execution_id: str, timestamp: datetime) -> str:
    """
    Generate the canonical summary filename: ``YYYYMMDD_HHMMSS_<exec8>.json``.

    Args:
        execution_id: Pipeline execution ID; first 8 chars are used.
        timestamp: Timestamp to format.

    Returns:
        Filename with ``.json`` extension. YAML/markdown variants ``.replace``
        the extension at the call site to preserve the date+exec prefix.
    """
    date_str = timestamp.strftime("%Y%m%d_%H%M%S")
    exec_short = execution_id[:8]
    return f"{date_str}_{exec_short}.json"
