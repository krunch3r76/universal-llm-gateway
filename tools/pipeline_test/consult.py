"""Consultation service: query models for prompt improvement suggestions.

Sends pipeline step context (prompt + output + problem description) to
consultant models via ``consult_lib.execute_consult()``.  Context assembly
and output truncation remain here; RAG, model selection, and query
execution are delegated to the shared library.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .models import ConsultResult, ModelCall, StepSnapshot

logger = logging.getLogger(__name__)

# Instructions appended to the user prompt for the prompt_engineer role.
_TASK_INSTRUCTIONS = (
    "## Your Task\n"
    "1. What specific issues do you see in the output given the problem description?\n"
    "2. What changes to the system prompt and/or user prompt would fix them?\n"
    "3. Provide the exact revised prompt text for each change you recommend."
)


def consult_step_via_lib(
    step: StepSnapshot,
    problem: str,
    *,
    call_label: str | None = None,
    models: list[str] | None = None,
    scope: str | list[str] | None = None,
    parallel: bool = False,
    stargate_url: str = "http://localhost:9999",
    timeout: float = 300.0,
    output_limit_chars: int | None = None,
    no_rag: bool = False,
    use_rag_pipeline: bool = True,
    rag_top_k: int | None = None,
) -> list[ConsultResult]:
    """Consult models about a pipeline step via consult_lib.

    Assembles step context locally, then delegates RAG retrieval,
    model selection, and querying to ``execute_consult()``.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from consult_lib.core import ConsultResult as LibResult
    from consult_lib.core import execute_consult

    context_text = _build_user_prompt(
        step=step,
        call_label=call_label,
        problem=problem,
        output_limit_chars=output_limit_chars,
    )

    effective_scope: str | list[str] = (
        scope
        if (scope is not None and (not isinstance(scope, list) or len(scope) > 0))
        else detect_scope(step)
    )

    lib_results: list[LibResult] = execute_consult(
        question=problem,
        role="prompt_engineer",
        context_text=context_text,
        scope=effective_scope,
        chain=not parallel,
        models=models,
        stargate_url=stargate_url,
        no_rag=no_rag,
        use_rag_pipeline=use_rag_pipeline,
        rag_top_k=rag_top_k,
        timeout=timeout,
    )

    subset = (
        "model_id",
        "response_text",
        "prompt_tokens",
        "completion_tokens",
        "latency_ms",
        "error",
    )
    return [ConsultResult(**{k: getattr(r, k) for k in subset}) for r in lib_results]


def detect_scope(step: StepSnapshot) -> str:
    """Detect RAG scope from the step's target model tier.

    Heuristic: ``"/" in model_id`` -> cloud -> ``llm_prompting``.
    Otherwise -> local -> ``small_llm_prompting``.
    Falls back to ``prompting`` (union) when undetermined.
    """
    model_id = _resolve_step_model_id(step)
    if model_id is None:
        logger.info(
            "Auto-scope: no model_id resolved for step '%s' -> 'prompting'",
            step.step_name,
        )
        return "prompting"

    if "/" in model_id:
        scope = "llm_prompting"
        logger.info(
            "Auto-scope: '%s' (model %s is cloud) -> '%s'. Override with --scope.",
            step.step_name,
            model_id,
            scope,
        )
    else:
        scope = "small_llm_prompting"
        logger.info(
            "Auto-scope: '%s' (model %s is local) -> '%s'. Override with --scope.",
            step.step_name,
            model_id,
            scope,
        )
    return scope


def _resolve_step_model_id(step: StepSnapshot) -> str | None:
    """Extract the model ID from the step's first model call."""
    if not step.model_calls:
        return None
    call = step.model_calls[0]
    return getattr(call, "model_id", None)


def estimate_fixed_chars(
    step: StepSnapshot,
    call_label: str | None,
    problem: str,
) -> tuple[int, int]:
    """Return *(fixed_chars, output_chars)* for budget computation.

    *fixed_chars* covers everything except model output and RAG findings.
    """
    call = _select_call(step, call_label)
    fixed = (
        len(f"## Pipeline Step: {step.step_name} ({step.step_type})")
        + len(f"## Problem\n{problem}")
        + len(f"## System Prompt Given to Model\n{call.system_prompt or ''}")
        + len(f"## User Prompt Given to Model\n{call.user_prompt}")
        + len(_TASK_INSTRUCTIONS)
        + 100
    )
    return fixed, len(call.response_text)


def _build_user_prompt(
    step: StepSnapshot,
    call_label: str | None,
    problem: str,
    output_limit_chars: int | None = None,
) -> str:
    """Package step context into a consultation prompt."""
    call = _select_call(step, call_label)

    sections: list[str] = [
        f"## Pipeline Step: {step.step_name} ({step.step_type})",
        f"## Problem\n{problem}",
    ]

    if call.system_prompt:
        sections.append(f"## System Prompt Given to Model\n{call.system_prompt}")

    sections.append(f"## User Prompt Given to Model\n{call.user_prompt}")

    output = call.response_text
    limit = output_limit_chars if output_limit_chars is not None else len(output)
    if len(output) > limit:
        output = (
            output[:limit]
            + f"\n\n[... truncated at {limit} of {len(call.response_text)} chars]"
        )
    sections.append(f"## Model Output\n{output}")
    sections.append(_TASK_INSTRUCTIONS)

    return "\n\n".join(sections)


def _select_call(step: StepSnapshot, call_label: str | None) -> ModelCall:
    """Find a call by label, or return the first."""
    if not step.model_calls:
        raise ValueError(f"Step '{step.step_name}' has no model calls")
    if call_label:
        for call in step.model_calls:
            if call.call_label == call_label:
                return call
        labels = [c.call_label for c in step.model_calls]
        raise KeyError(f"Call '{call_label}' not found. Available: {labels}")
    return step.model_calls[0]
