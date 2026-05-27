"""
Per-step markdown file renderer for ``write_step_summaries``.

Produces the contents of an individual ``NN_<step_id>.md`` file written into
a pipeline execution directory. Format is richer than the per-step section of
the full summary — includes a ``## Handler Inputs`` section resolved from the
step's ``handler_inputs`` bindings, per-iteration input context for map steps,
and a chunked-verification statement-count breakdown per iteration.

Distinct from ``full_summary._render_step_section``: keep both. The full-
summary section is inline within a larger document; this module renders a
standalone file with its own header, metadata, and configuration block.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ...core.execution.map_reduce.collection import MapOutputCollection
from ...execution_summary_inputs import (
    format_handler_inputs_section,
    format_map_iteration_inputs,
)
from .aggregate import build_aggregate_summary
from .verification import build_verification_summary

if TYPE_CHECKING:
    from ...core.handlers.protocol import PipelineContext


def render_step_markdown(
    step_id: str,
    step_index: int,
    output: Any,
    spec: Any,
    context: PipelineContext,
) -> str:
    """
    Render a single step as standalone markdown for a per-step file.

    Args:
        step_id: Step identifier.
        step_index: 1-based step index (matches the 0-padded filename prefix).
        output: ``StepOutput`` or ``MapOutputCollection``.
        spec: Step specification (may be ``None``; affects type/config display).
        context: Pipeline execution context.

    Returns:
        Markdown file body as a single newline-joined string.
    """
    is_map_output = isinstance(output, MapOutputCollection)

    lines = [
        f"# Step {step_index}: {step_id}",
        "",
        "## Metadata",
        f"- **Step ID**: `{step_id}`",
        f"- **Type**: {spec.type if spec else 'unknown'}",
    ]

    if is_map_output:
        all_outputs = output.all_outputs()
        model_ids = sorted({o.model_id for o in all_outputs if o.model_id})
        latencies = [o.latency_ms for o in all_outputs if o.latency_ms]

        lines.extend(
            [
                f"- **Models**: {', '.join(model_ids)}",
                f"- **Iterations**: {len(all_outputs)}",
                f"- **Total Latency**: {sum(latencies):.2f}ms",
                "",
            ]
        )

        input_lines = format_handler_inputs_section(spec, context)
        if input_lines:
            lines.extend(input_lines)

        prompt_tokens = sum(o.prompt_tokens for o in all_outputs)
        completion_tokens = sum(o.completion_tokens for o in all_outputs)
        total_tokens = prompt_tokens + completion_tokens

        if total_tokens > 0:
            lines.extend(
                [
                    "## Token Usage",
                    f"- **Prompt**: {prompt_tokens}",
                    f"- **Completion**: {completion_tokens}",
                    f"- **Total**: {total_tokens}",
                    "",
                ]
            )

        verification_summary = build_verification_summary(
            all_outputs, step_id, spec, context
        )
        if verification_summary:
            lines.extend(verification_summary)

        lines.extend(["## Iteration Outputs", ""])

        iteration_keys = getattr(output, "_keys", [None] * len(all_outputs))

        for j, iter_output in enumerate(all_outputs):
            latency_str = (
                f"- Latency: {iter_output.latency_ms:.2f}ms"
                if iter_output.latency_ms
                else ""
            )

            iteration_lines = [
                f"### Iteration {j + 1}",
                f"- Model: `{iter_output.model_id}`",
                latency_str,
                "",
            ]

            iter_json = getattr(iter_output, "json", None)
            if isinstance(iter_json, dict):
                chunked = iter_json.get("chunked_verification", {})
                if chunked.get("enabled") and "total_statements" in chunked:
                    total = chunked["total_statements"]
                    iteration_lines.append(f"- **Statements evaluated:** {total}")
                    domains = chunked.get("domains") or {}
                    if domains:
                        parts = [
                            f"{d}: {info.get('statements', 0)}"
                            for d, info in sorted(domains.items())
                        ]
                        iteration_lines.append(f"- **By domain:** {', '.join(parts)}")
                    iteration_lines.append("")

            iteration_key = iteration_keys[j] if j < len(iteration_keys) else None
            iter_input_lines = format_map_iteration_inputs(spec, j, None, iteration_key)
            if iter_input_lines:
                iteration_lines.extend(iter_input_lines)

            if iter_output.system_prompt or iter_output.user_prompt:
                iteration_lines.append("**Prompts:**")
                iteration_lines.append("")

                if iter_output.system_prompt:
                    iteration_lines.extend(
                        [
                            "*System:*",
                            "```",
                            iter_output.system_prompt,
                            "```",
                            "",
                        ]
                    )

                if iter_output.user_prompt:
                    iteration_lines.extend(
                        [
                            "*User:*",
                            "```",
                            iter_output.user_prompt,
                            "```",
                            "",
                        ]
                    )

            if iter_output.request_body:
                request_json = json.dumps(
                    iter_output.request_body, indent=2, ensure_ascii=False
                )
                iteration_lines.extend(
                    [
                        "**LLM Request Body:**",
                        "```json",
                        request_json,
                        "```",
                        "",
                    ]
                )

            has_raw = iter_output.raw and iter_output.raw.strip()
            has_json = hasattr(iter_output, "json") and iter_output.json

            if has_raw:
                iteration_lines.extend(
                    [
                        "**Output:**",
                        (
                            "```json"
                            if iter_output.raw.strip().startswith("{")
                            else "```"
                        ),
                        iter_output.raw,
                        "```",
                        "",
                    ]
                )
            elif has_json:
                iteration_lines.extend(
                    [
                        "**Output:**",
                        "```json",
                        json.dumps(iter_output.json, indent=2, ensure_ascii=False),
                        "```",
                        "",
                    ]
                )
            else:
                iteration_lines.extend(
                    [
                        "**Output:**",
                        "```",
                        "",
                        "```",
                        "",
                    ]
                )

            lines.extend(iteration_lines)

    else:
        latency_str = (
            f"- **Latency**: {output.latency_ms:.2f}ms" if output.latency_ms else ""
        )
        lines.extend(
            [
                f"- **Model**: `{output.model_id}`",
                latency_str,
                "",
            ]
        )

        input_lines = format_handler_inputs_section(spec, context)
        if input_lines:
            lines.extend(input_lines)

        total_tokens = output.prompt_tokens + output.completion_tokens
        if total_tokens > 0:
            lines.extend(
                [
                    "## Token Usage",
                    f"- **Prompt**: {output.prompt_tokens}",
                    f"- **Completion**: {output.completion_tokens}",
                    f"- **Total**: {total_tokens}",
                    "",
                ]
            )

        aggregate_summary = build_aggregate_summary(step_id, output)
        if aggregate_summary:
            lines.extend(aggregate_summary)

        if spec:
            temp_display = (
                f"{output.temperature:.2f}" if output.temperature is not None else "N/A"
            )
            max_tokens_display = (
                str(output.max_tokens) if output.max_tokens is not None else "N/A"
            )

            lines.extend(
                [
                    "## Configuration",
                    f"- **Model Ref**: `{getattr(spec, 'model_ref', 'N/A')}`",
                    f"- **Prompt Ref**: `{getattr(spec, 'prompt_ref', 'N/A')}`",
                    f"- **Temperature**: {temp_display}",
                    f"- **Max Tokens**: {max_tokens_display}",
                    "",
                ]
            )

        if output.system_prompt or output.user_prompt:
            lines.extend(["## Prompts", ""])

            if output.system_prompt:
                lines.extend(
                    [
                        "### System Prompt",
                        "```",
                        output.system_prompt,
                        "```",
                        "",
                    ]
                )

            if output.user_prompt:
                lines.extend(
                    [
                        "### User Prompt",
                        "```",
                        output.user_prompt,
                        "```",
                        "",
                    ]
                )

        if output.request_body:
            lines.extend(
                [
                    "## LLM Request Body",
                    "```json",
                    json.dumps(output.request_body, indent=2, ensure_ascii=False),
                    "```",
                    "",
                ]
            )

        lines.extend(
            [
                "## Output",
                "",
                "### Raw",
                "```json" if output.raw.strip().startswith("{") else "```",
                output.raw,
                "```",
                "",
            ]
        )

        if output.json:
            lines.extend(
                [
                    "### JSON Data",
                    "```json",
                    json.dumps(output.json, indent=2, ensure_ascii=False),
                    "```",
                    "",
                ]
            )

    return "\n".join(lines)
