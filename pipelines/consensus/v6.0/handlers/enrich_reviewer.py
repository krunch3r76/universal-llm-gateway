"""
Review enriched answer for missing facts and re-enrich if needed.

Iterative quality gate: check → revise → re-check, up to max_review_rounds.
Exits early when no missing facts. Covers cases where one revision pass
cannot incorporate all missing facts.

Contract:
    Inputs: enriched_answer, verified_facts, question
    Outputs: raw (enriched answer), json.review_rounds, json.final_missing
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from .shared._chain_post_process import _MIN_SYNTHESIS_TOKENS, _TOKENS_PER_FACT

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_CHECK_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "missing_indices": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
        },
    },
    "required": ["missing_indices"],
    "additionalProperties": False,
}


class EnrichReviewerHandler(BaseHandler):
    """Check enriched answer for missing facts and re-enrich if needed."""

    step_type: str = "consensus_enrich_review_v5"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Review enrichment completeness; iterative check→revise up to max_rounds."""
        start_time = time.time()
        resolver = NamespaceResolver(context)

        enriched_answer = str(
            self._resolve_input(resolver, step, "enriched_answer", step.handler_inputs)
            or ""
        )
        verified_facts: list[dict[str, Any]] = (
            self._resolve_input(resolver, step, "verified_facts", step.handler_inputs)
            or []
        )
        question = str(
            self._resolve_input(resolver, step, "question", step.handler_inputs) or ""
        )

        if not enriched_answer or not verified_facts:
            logger.warning(
                "Step '%s': empty enriched_answer or verified_facts — passthrough",
                step.id,
            )
            return StepOutput(raw=enriched_answer, step_id=step.id)

        numbered_facts = _format_numbered_facts(verified_facts)
        max_rounds: int = int(step.get_domain_field("max_review_rounds") or 2)

        answer = enriched_answer
        total_prompt_tokens = 0
        total_completion_tokens = 0
        rounds_used = 0
        last_check_missing = 0

        for round_idx in range(max_rounds):
            rounds_used = round_idx + 1
            answer, missing_count, pt, ct = await self._check_and_revise(
                step,
                context,
                answer,
                numbered_facts,
                verified_facts,
                question,
            )
            total_prompt_tokens += pt
            total_completion_tokens += ct
            last_check_missing = missing_count
            logger.info(
                "Step '%s': round %d — %d missing facts (pre-revision)",
                step.id,
                rounds_used,
                missing_count,
            )
            if missing_count == 0:
                break

        final_missing = last_check_missing
        if last_check_missing > 0:
            final_missing, pt = await self._check_only(
                step, context, answer, numbered_facts, verified_facts
            )
            total_prompt_tokens += pt
            logger.info(
                "Step '%s': post-revision check — %d facts still missing",
                step.id,
                final_missing,
            )

        latency_ms = (time.time() - start_time) * 1000
        return StepOutput(
            raw=answer,
            step_id=step.id,
            latency_ms=latency_ms,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            json={
                "review_rounds": rounds_used,
                "last_check_missing": last_check_missing,
                "final_missing": final_missing,
            },
        )

    async def _check_only(
        self,
        step: StepConfig,
        context: PipelineContext,
        answer: str,
        numbered_facts: str,
        verified_facts: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """Check-only pass (no revision). Returns (missing_count, prompt_tokens)."""
        check_prompt_ref = step.prompt_ref
        assert check_prompt_ref and step.model_ref
        rendered = self._render_prompt(
            check_prompt_ref,
            {
                "enriched_answer": answer,
                "numbered_facts": numbered_facts,
                "question": "",
            },
            context,
            safe=True,
        )
        model_id = self._resolve_model_alias(step.model_ref, context)
        result = await self._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            system_prompt=rendered.system_prompt,
            temperature=0.0,
            max_tokens=self._resolve_max_tokens(step, context, handler_default=512),
            json_schema=_CHECK_JSON_SCHEMA,
        )
        missing = _parse_missing_indices(result.content, len(verified_facts))
        return (len(missing), result.prompt_tokens or 0)

    async def _check_and_revise(
        self,
        step: StepConfig,
        context: PipelineContext,
        enriched_answer: str,
        numbered_facts: str,
        verified_facts: list[dict[str, Any]],
        question: str,
    ) -> tuple[str, int, int, int]:
        """One round: check for missing facts; if any, revise and return (answer, missing_count, pt, ct)."""
        check_prompt_ref = step.prompt_ref
        if not check_prompt_ref:
            raise ValueError(f"Step '{step.id}' missing prompt_ref")
        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")

        rendered_check = self._render_prompt(
            check_prompt_ref,
            {
                "enriched_answer": enriched_answer,
                "numbered_facts": numbered_facts,
                "question": question,
            },
            context,
            safe=True,
        )
        model_id = self._resolve_model_alias(step.model_ref, context)
        check_result = await self._call_model(
            model_id,
            rendered_check.user_prompt,
            step,
            context,
            system_prompt=rendered_check.system_prompt,
            temperature=0.0,
            max_tokens=self._resolve_max_tokens(step, context, handler_default=512),
            json_schema=_CHECK_JSON_SCHEMA,
        )
        missing_indices = _parse_missing_indices(
            check_result.content, len(verified_facts)
        )
        pt = check_result.prompt_tokens or 0
        ct = check_result.completion_tokens or 0

        if not missing_indices:
            return (enriched_answer, 0, pt, ct)

        missing_facts_text = _format_missing_facts(verified_facts, missing_indices)
        re_enrich_ref = str(step.get_domain_field("prompt_ref_re_enrich") or "")
        if not re_enrich_ref:
            raise ValueError(f"Step '{step.id}' missing prompt_ref_re_enrich")
        rendered_re_enrich = self._render_prompt(
            re_enrich_ref,
            {
                "enriched_answer": enriched_answer,
                "missing_facts": missing_facts_text,
                "question": question,
            },
            context,
            safe=True,
        )
        gen_params = step.generation_parameters or {}
        dynamic_budget = max(
            _MIN_SYNTHESIS_TOKENS, _TOKENS_PER_FACT * len(verified_facts)
        )
        re_enrich_result = await self._call_model(
            model_id,
            rendered_re_enrich.user_prompt,
            step,
            context,
            system_prompt=rendered_re_enrich.system_prompt,
            temperature=gen_params.get("temperature", 0.3),
            max_tokens=self._resolve_max_tokens(
                step, context, handler_default=dynamic_budget
            ),
        )
        final_answer = re_enrich_result.content.strip()
        pt += re_enrich_result.prompt_tokens or 0
        ct += re_enrich_result.completion_tokens or 0
        return (final_answer, len(missing_indices), pt, ct)

    @override
    def validate(self, step: StepConfig) -> list[str]:
        """Validate step configuration."""
        errors: list[str] = []
        if not step.model_ref:
            errors.append(f"Step '{step.id}' missing model_ref")
        if not step.prompt_ref:
            errors.append(f"Step '{step.id}' missing prompt_ref (check_enrichment)")
        if not step.get_domain_field("prompt_ref_re_enrich"):
            errors.append(f"Step '{step.id}' missing prompt_ref_re_enrich")
        inputs = step.handler_inputs or {}
        for required in ("enriched_answer", "verified_facts"):
            if required not in inputs:
                errors.append(
                    f"Step '{step.id}' missing '{required}' in handler_inputs"
                )
        return errors


def _format_numbered_facts(facts: list[dict[str, Any]]) -> str:
    """Format verified facts as 1-indexed numbered list for the prompt."""
    lines: list[str] = []
    for i, fact in enumerate(facts, start=1):
        text = (
            fact.get("text", str(fact)).strip()
            if isinstance(fact, dict)
            else str(fact).strip()
        )
        if text:
            lines.append(f"[{i}] {text}")
    return "\n".join(lines)


def _format_missing_facts(
    facts: list[dict[str, Any]], missing_indices: list[int]
) -> str:
    """Format only the missing facts as a numbered list (1-indexed)."""
    lines: list[str] = []
    for idx in sorted(missing_indices):
        fact = facts[idx - 1]
        text = (
            fact.get("text", str(fact)).strip()
            if isinstance(fact, dict)
            else str(fact).strip()
        )
        if text:
            lines.append(f"[{idx}] {text}")
    return "\n".join(lines)


def _parse_missing_indices(raw_response: str, fact_count: int) -> list[int]:
    """Parse LLM response into validated list of missing fact indices (1-indexed)."""
    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse check_enrichment response: %s", e)
        return []

    indices = data.get("missing_indices", [])
    if not isinstance(indices, list):
        logger.warning("missing_indices is not a list: %s", type(indices))
        return []

    valid: list[int] = []
    for idx in indices:
        if isinstance(idx, int) and 1 <= idx <= fact_count:
            valid.append(idx)
        else:
            logger.warning(
                "Ignoring out-of-range missing index: %s (valid=1..%d)",
                idx,
                fact_count,
            )
    return valid
