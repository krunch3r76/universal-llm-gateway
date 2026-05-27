"""
Full-summary markdown renderer — single source of truth.

Produces the complete pipeline-execution markdown summary used by both the
single-file ``write_summary_markdown`` path and the ``full_summary.md`` file
inside the per-step execution directory written by ``write_step_summaries``.

This module replaces the previously-duplicated rendering in
``ExecutionSummaryWriter.write_summary_markdown`` and
``ExecutionSummaryWriter._build_full_markdown``. The consolidated output
follows the strict-superset shape of the former ``_build_full_markdown`` —
it includes the per-model statement-count block (for map steps with chunked
verification) and the aggregate-summary block (for aggregate steps with math
rejections) that the single-file path previously omitted.

Private helper ``_render_step_section`` renders each per-step block; it is
module-private and not part of the markdown package's public surface.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ...core.execution.map_reduce.collection import MapOutputCollection
from .aggregate import build_aggregate_summary
from .token_table import build_token_summary_table

if TYPE_CHECKING:
    from ...core.handlers.protocol import PipelineContext
    from ...core.schemas import PipelineSpec


def render_full_summary_markdown(
    pipeline: PipelineSpec,
    context: PipelineContext,
    request_body: dict[str, Any],
    response_body: dict[str, Any],
    execution_order: list[str] | None,
    timestamp: datetime,
) -> str:
    """
    Render the full pipeline-execution markdown summary.

    Args:
        pipeline: Pipeline specification.
        context: Pipeline execution context with step outputs.
        request_body: Original request body (chat-completion request shape).
        response_body: Final response body (OpenAI-compatible).
        execution_order: Optional list of step IDs in execution order. Falls
            back to ``context.outputs`` insertion order when ``None``.
        timestamp: Timestamp to embed in the metadata header.

    Returns:
        Assembled markdown body as a single string (newline-joined).
    """
    execution_time = (datetime.now() - context.started_at).total_seconds() * 1000

    lines = [
        f"# Pipeline Execution Summary: {pipeline.id}",
        "",
        "## Metadata",
        f"- **Pipeline ID**: `{pipeline.id}`",
        f"- **Version**: {pipeline.version}",
        f"- **Type**: {pipeline.domain}",
        f"- **Execution ID**: `{context.execution_id}`",
        f"- **Timestamp**: {timestamp.isoformat()}",
        f"- **Execution Time**: {execution_time:.2f}ms",
        "",
        "## Request",
        "### Source Text",
        "```",
        context.source_text,
        "```",
        "",
        "### Full Request Body",
        "```json",
        json.dumps(request_body, indent=2, ensure_ascii=False),
        "```",
        "",
    ]

    step_specs = {step.id: step for step in pipeline.steps}
    ordered_steps = execution_order or list(context.outputs.keys())
    lines.extend(build_token_summary_table(context, ordered_steps))
    lines.extend(["## Execution Steps", ""])

    for i, step_id in enumerate(ordered_steps, 1):
        output = context.outputs.get(step_id)
        spec = step_specs.get(step_id)
        lines.extend(_render_step_section(step_id, i, output, spec))

    lines.extend(
        [
            "## Final Response",
            "```json",
            json.dumps(response_body, indent=2, ensure_ascii=False),
            "```",
            "",
            "## Pipeline Options",
            "```json",
            json.dumps(pipeline.options.model_dump(), indent=2, ensure_ascii=False),
            "```",
        ]
    )

    return "\n".join(lines)


def _render_step_section(
    step_id: str,
    step_index: int,
    output: Any,
    spec: Any,
) -> list[str]:
    """
    Render a single per-step section for the full summary.

    Handles both single-output steps and ``MapOutputCollection`` map steps,
    with branching for configuration display, prompt/request-body inclusion
    (non-map only), aggregate-summary inclusion (non-map aggregate steps),
    and per-model statement-count enumeration (map steps with chunked
    verification).

    Returns an empty list when ``output`` is ``None``.
    """
    if output is None:
        return []

    is_map_output = isinstance(output, MapOutputCollection)

    if is_map_output:
        all_outputs = output.all_outputs()
        model_ids = sorted({o.model_id for o in all_outputs if o.model_id})
        model_display = ", ".join(model_ids) if model_ids else "N/A (map step)"
        latencies = [o.latency_ms for o in all_outputs if o.latency_ms]
        latency_display = (
            f"{sum(latencies):.2f}ms (total, {len(all_outputs)} iterations)"
            if latencies
            else "N/A"
        )
        prompt_tokens = sum(o.prompt_tokens for o in all_outputs)
        completion_tokens = sum(o.completion_tokens for o in all_outputs)
    else:
        model_display = output.model_id or "N/A"
        latency_display = f"{output.latency_ms:.2f}ms" if output.latency_ms else "N/A"
        prompt_tokens = output.prompt_tokens
        completion_tokens = output.completion_tokens

    lines = [
        f"### Step {step_index}: {step_id}",
        "",
        f"- **Type**: {spec.type if spec else 'unknown'}",
        f"- **Model**: {model_display}",
        f"- **Latency**: {latency_display}",
        "",
    ]

    total_tokens = prompt_tokens + completion_tokens
    if total_tokens > 0:
        lines.extend(
            [
                "**Token Usage:**",
                f"- Prompt Tokens: {prompt_tokens}",
                f"- Completion Tokens: {completion_tokens}",
                f"- Total Tokens: {total_tokens}",
                "",
            ]
        )

    # Aggregate summary: parent rejections (math) with authority reasons.
    # Only meaningful for non-map outputs (the json shape is per-step, not
    # per-iteration).
    if not is_map_output:
        aggregate_summary = build_aggregate_summary(step_id, output)
        if aggregate_summary:
            lines.extend(aggregate_summary)

    if spec and not is_map_output:
        temp_display = (
            f"{output.temperature:.2f}" if output.temperature is not None else "N/A"
        )
        max_tokens_display = (
            str(output.max_tokens) if output.max_tokens is not None else "N/A"
        )

        lines.extend(
            [
                "**Configuration:**",
                f"- Model Ref: `{getattr(spec, 'model_ref', 'N/A')}`",
                f"- Prompt Ref: `{getattr(spec, 'prompt_ref', 'N/A')}`",
                f"- Temperature: {temp_display}",
                f"- Max Tokens: {max_tokens_display}",
                f"- Depends On: {getattr(spec, 'depends_on', [])}",
                "",
            ]
        )
    elif spec and is_map_output:
        lines.extend(
            [
                "**Configuration:**",
                f"- Model Ref: `{getattr(spec, 'model_ref', 'N/A')}`",
                f"- Iterations: {len(output)}",
                f"- Depends On: {getattr(spec, 'depends_on', [])}",
                "",
            ]
        )

    if not is_map_output and (output.system_prompt or output.user_prompt):
        lines.append("**Prompts Sent to Model:**")
        lines.append("")

        if output.system_prompt:
            lines.extend(
                [
                    "*System Prompt:*",
                    "```",
                    output.system_prompt,
                    "```",
                    "",
                ]
            )

        if output.user_prompt:
            lines.extend(
                [
                    "*User Prompt:*",
                    "```",
                    output.user_prompt,
                    "```",
                    "",
                ]
            )

    if not is_map_output and output.request_body:
        lines.extend(
            [
                "**LLM Request Body:**",
                "```json",
                json.dumps(output.request_body, indent=2, ensure_ascii=False),
                "```",
                "",
            ]
        )

    if is_map_output:
        all_outputs = output.all_outputs()
        lines.extend(
            [
                f"**Map Output:** {len(all_outputs)} iteration(s)",
                "",
            ]
        )
        # Per-model statement counts (chunked verification)
        per_model = []
        for o in all_outputs:
            ojson = getattr(o, "json", None) or {}
            chunked = ojson.get("chunked_verification", {})
            n = chunked.get("total_statements")
            mid = getattr(o, "model_id", None) or "unknown"
            if n is not None:
                per_model.append(f"{mid}: {n} statements")
        if per_model:
            lines.extend(
                [
                    "**Statements evaluated per model:** " + ", ".join(per_model),
                    "",
                ]
            )
        if all_outputs:
            sample = all_outputs[0]
            lines.extend(
                [
                    "*Full output (first iteration):*",
                    "```json" if sample.raw.strip().startswith("{") else "```",
                    sample.raw,
                    "```",
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "**Raw Output:**",
                "```json" if output.raw.strip().startswith("{") else "```",
                output.raw,
                "```",
                "",
            ]
        )

        if output.json:
            lines.extend(
                [
                    "**JSON Data:**",
                    "```json",
                    json.dumps(output.json, indent=2, ensure_ascii=False),
                    "```",
                    "",
                ]
            )

        if output.text != output.raw:
            lines.extend(
                [
                    "**Extracted Text:**",
                    "```",
                    output.text,
                    "```",
                    "",
                ]
            )

    return lines
