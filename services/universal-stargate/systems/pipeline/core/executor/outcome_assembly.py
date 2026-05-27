"""Assemble a ``PipelineExecutionOutcome`` from a completed DAG run.

Extracted from ``execution_loop.run_prepared_execution_inner`` so the
lifecycle-event try/except surface and the post-success aggregation
(token totals, reasoning walk, hints, model_entity_id, final text) live
in separate modules under the 300-SLOC ceiling.

Pure post-DAG read of ``pipeline_context.outputs`` and
``dag_executor.execution_order``; no events, no side effects beyond a
single completion log emission on the shared ``execution_logger``.
"""

from __future__ import annotations

from typing import Any

from ..execution.outcome import PipelineExecutionOutcome, extract_model_entity_id
from ..handlers import StepOutput
from .output_resolution import (
    extract_backtranslation_data,
    extract_output_hints,
    get_final_result,
)
from .prepared import PreparedPipelineExecution, execution_logger


def assemble_outcome(
    prepared: PreparedPipelineExecution,
    duration: float,
) -> PipelineExecutionOutcome:
    """Build the outcome carrier from the just-finished DAG.

    Walks ``pipeline_context.outputs`` for token totals (including
    ``MapOutputCollection`` aggregation), reverse-walks ``execution_order``
    for the terminal reasoning trace, resolves the final text via
    ``get_final_result``, and packages everything plus structured hints
    and the canonical Cortex model entity id.
    """
    pipeline = prepared.pipeline
    pipeline_context = prepared.pipeline_context
    dag_executor = prepared.dag_executor

    final_result = get_final_result(pipeline, pipeline_context, prepared.output_aliases)

    step_outputs = {
        step_id: output.text
        for step_id, output in pipeline_context.outputs.items()
        if isinstance(output, StepOutput)
    }

    backtranslation_data = extract_backtranslation_data(
        prepared.steps, pipeline_context
    )

    prompt_tokens = 0
    completion_tokens = 0
    reasoning_tokens = 0
    for out in pipeline_context.outputs.values():
        from ..execution.map_reduce.collection import MapOutputCollection

        if isinstance(out, MapOutputCollection):
            prompt_tokens += sum(inner.prompt_tokens for inner in out.all_outputs())
            completion_tokens += sum(
                inner.completion_tokens for inner in out.all_outputs()
            )
            reasoning_tokens += sum(
                getattr(inner, "reasoning_tokens", 0) for inner in out.all_outputs()
            )
        elif isinstance(out, StepOutput):
            prompt_tokens += out.prompt_tokens
            completion_tokens += out.completion_tokens
            reasoning_tokens += getattr(out, "reasoning_tokens", 0)

    # Reasoning is a final-step concern, not aggregatable. Walk execution
    # order in reverse and take the first StepOutput with a non-None
    # reasoning value — mirrors how ``final_result`` selects terminal text.
    reasoning: Any = None
    for step_id in reversed(dag_executor.execution_order):
        out = pipeline_context.outputs.get(step_id)
        if isinstance(out, StepOutput) and out.reasoning is not None:
            reasoning = out.reasoning
            break

    execution_logger.info(
        f"Pipeline execution completed: pipeline={pipeline.id}, "
        f"execution_id={pipeline_context.execution_id}, "
        f"duration={duration:.2f}s, steps={len(pipeline_context.outputs)}"
    )

    hints = extract_output_hints(pipeline, pipeline_context, prepared.output_aliases)
    model_entity_id = extract_model_entity_id(
        pipeline_context,
        list(dag_executor.execution_order),
    )

    return PipelineExecutionOutcome(
        execution_id=pipeline_context.execution_id,
        content=final_result,
        model=pipeline.id,
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            # Subset of completion_tokens; surfaced separately so consumers
            # can distinguish reasoning spend from visible output.
            "reasoning_tokens": reasoning_tokens,
        },
        duration_s=duration,
        step_outputs=step_outputs,
        backtranslation=backtranslation_data,
        execution_order=list(dag_executor.execution_order),
        reasoning=reasoning,
        model_entity_id=model_entity_id,
        hints=hints,
    )
