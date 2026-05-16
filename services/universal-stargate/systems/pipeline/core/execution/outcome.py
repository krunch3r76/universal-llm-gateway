"""Terminal pipeline execution outcome — DAG-result carrier consumed by
sync response construction and async tracker writes.

Lifted from ``executor.py`` to reduce its SLOC (767 → 741) and to give
the outcome shape + its extraction helper a single canonical home distinct
from the orchestration class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..handlers import PipelineContext, StepOutput


@dataclass(slots=True, kw_only=True)
class PipelineExecutionOutcome:
    """Structured terminal outcome of a successful pipeline execution.

    Decouples DAG results from FastAPI ``Response`` so async tracker writes
    and sync response construction consume the same canonical data without
    one path having to re-parse ``Response.body`` produced by the other.
    """

    execution_id: str
    content: str
    model: str
    usage: dict[str, Any] | None
    duration_s: float
    step_outputs: dict[str, str]
    backtranslation: dict[str, Any] | None
    execution_order: list[str]
    # Reasoning trace (when any step produced one). Shape preserved from
    # upstream — structured blocks or a flat string. Consumers can stringify;
    # they cannot un-flatten.
    reasoning: Any = None
    # Canonical Cortex model entity id for the terminal model, when the
    # terminal step exposes one. Kept separate from ``model`` so async pollers
    # retain the historical model field semantics.
    model_entity_id: str | None = None
    # Structured anomaly/advisory hints extracted from the terminal output
    # step's ``StepOutput.json["hints"]`` (e.g. ``output_short`` from
    # frontier dispatch). Threaded into ``PipelineExecutionResult.hints`` so
    # async pollers and bus subscribers can triage silent failures without
    # consulting the event service. ``None`` when no hints were produced.
    hints: list[dict[str, Any]] | None = None


def extract_model_entity_id(
    pipeline_context: PipelineContext,
    execution_order: list[str],
) -> str | None:
    """Return the terminal Cortex model entity id when a step exposes one.

    Walks ``execution_order`` in reverse and returns the first
    ``StepOutput.json['model_entity_id']`` it finds — mirrors how the
    terminal text is selected, so the entity id always tracks the step
    whose output became the pipeline's final answer.
    """
    for step_id in reversed(execution_order):
        out = pipeline_context.outputs.get(step_id)
        if not isinstance(out, StepOutput):
            continue
        if isinstance(out.json, dict):
            entity_id = out.json.get("model_entity_id")
            if isinstance(entity_id, str) and entity_id:
                return entity_id
    return None
