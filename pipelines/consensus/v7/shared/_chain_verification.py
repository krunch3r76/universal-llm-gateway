"""
Verification orchestration for chain enrichment.

Fan-out verification across models, individual and chunked execution.

Verdict shape: each leaf in verdicts_by_model is {"v": bool, "r": str}
where "v" is the boolean verdict and "r" is the model's reasoning.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from provenance.cross_model import group_by_eligible_models
from systems.pipeline.core.execution.chunked import ModelExecutionConfig
from universal_logging import get_logger

from ._chain_utils import strip_json_fences, token_budget
from ._chain_verification_chunked import verify_batch_chunked
from ._verdict import normalize_verdict
from .v4_types import VerdictEntry, VerificationModelTiming

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.builtin import BaseHandler
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

NEIGHBOR_WINDOW = 1


def _build_neighbor_context(candidates: list[dict[str, Any]], current_idx: int) -> str:
    """Build context from neighboring claims."""
    lines: list[str] = []
    start = max(0, current_idx - NEIGHBOR_WINDOW)
    end = min(len(candidates), current_idx + NEIGHBOR_WINDOW + 1)
    for i in range(start, end):
        if i == current_idx:
            continue
        text = candidates[i].get("text", "")
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines) if lines else "(none)"


def _format_claim_text_individual(candidate: dict[str, Any]) -> str:
    """Format a single claim with XML tags when context is available.

    Wraps in ``<topic name="..."><claim>...</claim></topic>`` when
    ``context_prefix`` and ``original_text`` are present, otherwise
    returns the flat ``text`` field.
    """
    topic = candidate.get("context_prefix", "")
    original = candidate.get("original_text", "")
    if topic and original:
        return f'<topic name="{topic}">\n<claim>{original}</claim>\n</topic>'
    return candidate.get("text", "")


async def verify_claims(
    handler: BaseHandler,
    candidates: list[dict[str, Any]],
    question: str,
    verify_model_ids: list[str],
    step: StepConfig,
    context: PipelineContext,
    prompt_ref: str,
    exec_configs: dict[str, ModelExecutionConfig] | None = None,
    prompt_ref_verify_batch: str | None = None,
) -> tuple[
    dict[str, list[bool]],
    dict[str, dict[str, VerdictEntry]],
    list[VerificationModelTiming],
]:
    """Verify candidates across verifier models.

    Fan-out: all models are dispatched concurrently via TaskGroup.
    Fan-in: aggregate per-claim verdicts from all models.

    The inference server is FIFO — all requests are submitted immediately
    and the server manages execution order. Serialising dispatch at this
    layer only adds wall-clock latency without any locality benefit.

    Returns:
        (verdicts, verdicts_by_model, model_timings)
        - verdicts: {statement_id: [bool, ...]} — flat bools for threshold logic.
        - verdicts_by_model: {model_id: {statement_id: {"v": bool, "r": str}}}.
        - model_timings: Per-model timing details including chunk breakdown.
    """
    gen_params = step.generation_parameters or {}
    gen_params.setdefault("repeat_penalty", 1.15)
    model_total = len(verify_model_ids)
    model_completed = 0
    eligible_by_model = group_by_eligible_models(
        candidates,
        models=verify_model_ids,
        exclude_origin=True,
        provenance_field="provenance",
    )

    async def _verify_one_model(
        model_id: str,
    ) -> tuple[str, dict[str, VerdictEntry], VerificationModelTiming]:
        eligible = eligible_by_model.get(model_id, candidates)
        exec_config = exec_configs.get(model_id) if exec_configs else None
        results, timing = await _verify_batch(
            handler=handler,
            eligible=eligible,
            question=question,
            model_id=model_id,
            step=step,
            context=context,
            prompt_ref=prompt_ref,
            gen_params=gen_params,
            exec_config=exec_config,
            prompt_ref_verify_batch=prompt_ref_verify_batch,
        )
        nonlocal model_completed
        model_completed += 1
        items_total = len(candidates)
        items_completed = (
            int((items_total * model_completed) / model_total) if model_total else 0
        )
        handler._report_progress(
            step,
            context,
            items_total=items_total,
            items_completed=items_completed,
            models_total=model_total,
            models_completed=model_completed,
        )
        return (model_id, results, timing)

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(_verify_one_model(mid)) for mid in verify_model_ids]
    model_results = [t.result() for t in tasks]

    verdicts: dict[str, list[bool]] = {}
    verdicts_by_model: dict[str, dict[str, VerdictEntry]] = {}
    model_timings: list[VerificationModelTiming] = []
    for model_id, results, timing in model_results:
        verdicts_by_model[model_id] = dict(results)
        model_timings.append(timing)
        for sid, vr in results.items():
            verdicts.setdefault(sid, []).append(vr["v"])

    return (verdicts, verdicts_by_model, model_timings)


async def _verify_batch(
    handler: BaseHandler,
    eligible: list[dict[str, Any]],
    question: str,
    model_id: str,
    step: StepConfig,
    context: PipelineContext,
    prompt_ref: str,
    gen_params: dict[str, Any],
    exec_config: ModelExecutionConfig | None = None,
    prompt_ref_verify_batch: str | None = None,
) -> tuple[dict[str, VerdictEntry], VerificationModelTiming]:
    """Verify all eligible claims for one model; individual or chunked by exec_config."""
    if exec_config is None or exec_config.chunk_size == 1:
        return await _verify_batch_individual(
            handler=handler,
            eligible=eligible,
            question=question,
            model_id=model_id,
            step=step,
            context=context,
            prompt_ref=prompt_ref,
            gen_params=gen_params,
        )
    batch_ref = prompt_ref_verify_batch or "consensus.v7.verify_batch_specific"
    return await verify_batch_chunked(
        handler=handler,
        eligible=eligible,
        question=question,
        model_id=model_id,
        step=step,
        context=context,
        gen_params=gen_params,
        exec_config=exec_config,
        prompt_ref_batch=batch_ref,
    )


async def _verify_batch_individual(
    handler: BaseHandler,
    eligible: list[dict[str, Any]],
    question: str,
    model_id: str,
    step: StepConfig,
    context: PipelineContext,
    prompt_ref: str,
    gen_params: dict[str, Any],
) -> tuple[dict[str, VerdictEntry], VerificationModelTiming]:
    """Verify all eligible claims for one model, one call per claim (TaskGroup)."""
    start = time.time()
    results: dict[str, VerdictEntry] = {}

    async def _verify_one(idx: int, candidate: dict[str, Any]) -> None:
        claim_text = _format_claim_text_individual(candidate)
        neighbor_context = _build_neighbor_context(eligible, idx)
        parent_text = candidate.get("parent_text", "")
        parent_claim_context = (
            f"ORIGINAL COMPOUND CLAIM (this sub-claim was extracted from):\n{parent_text}"
            if parent_text
            else "(none)"
        )

        rendered = handler._render_prompt(
            prompt_ref,
            {
                "cleaned_question": question,
                "claim_text": claim_text,
                "neighbor_context": neighbor_context,
                "parent_claim_context": parent_claim_context,
            },
            context,
            safe=True,
        )

        try:
            call_result = await handler._call_model(
                model_id,
                rendered.user_prompt,
                step,
                context,
                system_prompt=rendered.system_prompt,
                temperature=gen_params.get("temperature", 0.0),
                max_tokens=handler._constrained_tokens(
                    token_budget(context, "verify_individual", 256), context
                ),
                call_label="verify",
                json_schema={
                    "type": "object",
                    "properties": {
                        "verdict": {"type": "boolean"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["verdict", "reasoning"],
                },
            )

            if call_result.finish_reason == "length":
                logger.warning(
                    "Chain verify individual: model '%s' stopped due to length limit on claim %d "
                    "(tokens: %d prompt + %d completion). Response may be incomplete.",
                    model_id,
                    idx,
                    call_result.prompt_tokens,
                    call_result.completion_tokens,
                )

            parsed = json.loads(strip_json_fences(call_result.content))
            verdict = normalize_verdict(parsed.get("verdict"))
            reasoning = parsed.get("reasoning", "")
        except Exception as e:
            logger.error("Chain verify: claim %d failed for %s: %s", idx, model_id, e)
            verdict = False
            reasoning = ""

        sid = candidate.get("statement_id", "")
        results[sid] = {"v": verdict, "r": reasoning}

    async with asyncio.TaskGroup() as tg:
        for idx, candidate in enumerate(eligible):
            tg.create_task(_verify_one(idx, candidate))

    latency_ms = (time.time() - start) * 1000
    timing = VerificationModelTiming(
        model_id=model_id,
        num_claims=len(eligible),
        latency_ms=latency_ms,
        mode="individual",
        chunk_size=1,
        chunks=[],
    )
    return results, timing
