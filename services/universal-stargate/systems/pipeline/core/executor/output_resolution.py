"""Terminal output resolution — picks the pipeline's final text from
``context.outputs``, extracts structured hints, and surfaces backtranslation
data when present.

This is the read-only complement to ``DAGExecutor.execute``: the DAG has
finished writing to ``context.outputs``; these helpers pick the correct
terminal cell, follow sub-pipeline aliases, and shape the result for the
sync ``ResponseBuilder`` / async tracker.
"""

from __future__ import annotations

import json
from typing import Any

from universal_logging import get_logger

from ..handlers import PipelineContext, StepOutput
from ..schemas import PipelineSpec, StepConfig

logger = get_logger(__name__)


def resolve_terminal_output_ref(
    pipeline: PipelineSpec,
    output_aliases: dict[str, str] | None,
) -> str:
    """Resolve ``pipeline.output`` through ``output_aliases``.

    Sub-pipelines emit aliases like ``synthesize`` →
    ``synthesize__review_synthesis``; callers that want the resolved
    terminal step name use this helper rather than inlining the
    alias-lookup pattern.
    """
    output_ref = pipeline.output
    if output_aliases and output_ref in output_aliases:
        return output_aliases[output_ref]
    return output_ref


def extract_output_hints(
    pipeline: PipelineSpec,
    context: PipelineContext,
    output_aliases: dict[str, str] | None = None,
) -> list[dict[str, Any]] | None:
    """Extract structured anomaly hints from the terminal output step's JSON.

    Mirrors ``get_final_result``'s alias-resolution logic so sub-pipeline
    output references (e.g. ``synthesize`` → ``synthesize__review_synthesis``)
    surface hints from the resolved terminal step rather than missing the
    parent-name lookup. Hints only apply to single ``StepOutput`` terminals;
    map-collection terminals and unresolved references return ``None``.
    """
    output_ref = resolve_terminal_output_ref(pipeline, output_aliases)
    output = context.get_output(output_ref)
    if not isinstance(output, StepOutput):
        return None
    if not isinstance(output.json, dict):
        return None
    hints = output.json.get("hints")
    if isinstance(hints, list):
        return hints
    return None


def get_final_result(
    pipeline: PipelineSpec,
    context: PipelineContext,
    output_aliases: dict[str, str] | None = None,
) -> str:
    """
    Get final result from pipeline output step.

    Handles:
    - Simple step references: "step_name" → StepOutput.text
    - Sub-pipeline references: "synthesize" → resolved via output_aliases
      to e.g. "synthesize__review_synthesis"
    - Map output with key: "step_name.key" → specific iteration's text
    - MapOutputCollection: concatenates all outputs with double newlines
    """
    from ..execution.map_reduce.collection import MapOutputCollection

    output_ref = pipeline.output

    if output_aliases and output_ref in output_aliases:
        resolved_ref = output_aliases[output_ref]
        logger.info(
            f"Pipeline output '{output_ref}' resolved via sub-pipeline "
            f"alias to '{resolved_ref}'"
        )
        output_ref = resolved_ref
    else:
        logger.info(
            f"Pipeline output '{output_ref}' — no alias resolved "
            f"(aliases={list(output_aliases.keys()) if output_aliases else None})"
        )

    output = context.get_output(output_ref)
    if output:
        if isinstance(output, MapOutputCollection):
            if pipeline.output_format == "json_array":
                results = []
                for item in output.outputs_aligned():
                    if item is not None:
                        results.append(item.json if item.json is not None else item.raw)
                    else:
                        results.append(None)
                return json.dumps(results)
            text_parts = [item.text for item in output.all_outputs()]
            return "\n\n".join(text_parts)
        text = output.text
        logger.info(
            f"Pipeline output '{output_ref}': text={text[:80]!r} "
            f"(raw={output.raw[:40]!r}, json={output.json is not None})"
        )
        return text

    if "." in output_ref:
        step_name, key = output_ref.split(".", 1)
        step_output = context.get_output(step_name)

        if step_output is None:
            logger.error(
                f"Output '{output_ref}': step '{step_name}' not found "
                f"or returned no output"
            )
            return ""

        if isinstance(step_output, MapOutputCollection):
            result = step_output.get_output_by_key(key)
            if result:
                return result.text
            logger.warning(
                f"Output '{output_ref}': iteration key '{key}' not found in map output"
            )
        else:
            logger.error(
                f"Output '{output_ref}': step '{step_name}' is not a "
                f"MapOutputCollection"
            )
        return ""

    available = list(context.outputs.keys())
    logger.error(
        f"Pipeline output '{output_ref}' not found in context.outputs. "
        f"Available keys: {available}"
    )
    return ""


def extract_backtranslation_data(
    steps: list[StepConfig],
    context: PipelineContext,
) -> dict[str, Any] | None:
    """Extract backtranslation data if present."""
    for step in steps:
        if step.type == "backtranslation":
            bt_output = context.get_output(step.id)
            if bt_output and bt_output.json:
                return bt_output.json
    return None
