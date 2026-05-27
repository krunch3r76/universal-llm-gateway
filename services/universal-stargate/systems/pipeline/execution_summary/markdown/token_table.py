"""
Pipeline token-usage markdown table.

Builds the ``## Pipeline Token Summary`` table — prompt/completion/total
tokens and model-call counts per step, plus a bold ``TOTAL`` row. Used by
the full-summary markdown renderer at the top of the execution-steps section.

Handles both single-output steps (``StepOutput``) and map steps
(``MapOutputCollection``) by aggregating tokens across iterations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.execution.map_reduce.collection import MapOutputCollection
from ...core.handlers.protocol import StepOutput

if TYPE_CHECKING:
    from ...core.handlers.protocol import PipelineContext


def build_token_summary_table(
    context: PipelineContext, execution_order: list[str]
) -> list[str]:
    """
    Build a markdown table summarizing token usage across all steps.

    Args:
        context: Pipeline context — outputs accessed via ``context.outputs``.
        execution_order: Step IDs in execution order; unknown IDs are skipped.

    Returns:
        Markdown lines including the header, one row per step, a TOTAL row,
        and a trailing blank line.
    """
    lines = [
        "## Pipeline Token Summary",
        "",
        "| Step | Prompt | Completion | Total | Calls |",
        "|------|--------|------------|-------|-------|",
    ]

    total_prompt = 0
    total_completion = 0
    total_calls = 0

    for step_id in execution_order:
        out = context.outputs.get(step_id)
        if out is None:
            continue

        if isinstance(out, MapOutputCollection):
            prompt = sum(o.prompt_tokens for o in out.all_outputs())
            comp = sum(o.completion_tokens for o in out.all_outputs())
            calls = sum(getattr(o, "model_call_count", 0) for o in out.all_outputs())
            if not calls:
                calls = len(list(out.all_outputs()))
        elif isinstance(out, StepOutput):
            prompt = out.prompt_tokens
            comp = out.completion_tokens
            calls = getattr(out, "model_call_count", 0)
        else:
            continue

        total = prompt + comp
        total_prompt += prompt
        total_completion += comp
        total_calls += calls

        lines.append(f"| {step_id} | {prompt:,} | {comp:,} | {total:,} | {calls} |")

    grand_total = total_prompt + total_completion
    lines.append(
        f"| **TOTAL** | **{total_prompt:,}** | **{total_completion:,}** "
        f"| **{grand_total:,}** | **{total_calls}** |"
    )
    lines.append("")

    return lines
