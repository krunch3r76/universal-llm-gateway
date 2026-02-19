"""
Review enriched answer for missing facts and re-enrich if needed.

Single-step quality gate: asks one model "which facts are NOT adequately
represented in this answer?" and, if any are missing, makes one more
enrich call with the missing facts highlighted.

Placed between enrich and post_process so the enriched answer reaching
post_process has maximum fact coverage.

Contract:
    Inputs: enriched_answer, verified_facts, question
    Outputs: raw (enriched answer — original or re-enriched)
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.events.verification import EnrichReviewCompleted
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

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
        """Review enrichment completeness; re-enrich on missing facts."""
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

        # 1. Check which facts are missing from enriched answer
        check_prompt_ref = step.prompt_ref
        if not check_prompt_ref:
            raise ValueError(f"Step '{step.id}' missing prompt_ref")

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

        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")
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

        total_prompt_tokens = check_result.prompt_tokens or 0
        total_completion_tokens = check_result.completion_tokens or 0

        # 2. If no missing facts, pass through enriched answer
        if not missing_indices:
            latency_ms = (time.time() - start_time) * 1000
            _emit_event(context, step, len(verified_facts), [], False, latency_ms)
            logger.info(
                "Step '%s': all %d facts present in enriched answer (%.0fms)",
                step.id,
                len(verified_facts),
                latency_ms,
            )
            return StepOutput(
                raw=enriched_answer,
                step_id=step.id,
                latency_ms=latency_ms,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
            )

        # 3. Re-enrich with missing facts highlighted
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
        re_enrich_result = await self._call_model(
            model_id,
            rendered_re_enrich.user_prompt,
            step,
            context,
            system_prompt=rendered_re_enrich.system_prompt,
            temperature=gen_params.get("temperature", 0.3),
            max_tokens=self._resolve_max_tokens(step, context, handler_default=4096),
        )

        final_answer = re_enrich_result.content.strip()
        total_prompt_tokens += re_enrich_result.prompt_tokens or 0
        total_completion_tokens += re_enrich_result.completion_tokens or 0
        latency_ms = (time.time() - start_time) * 1000

        _emit_event(
            context, step, len(verified_facts), missing_indices, True, latency_ms
        )

        logger.info(
            "Step '%s': %d/%d facts missing — re-enriched %d → %d chars (%.0fms)",
            step.id,
            len(missing_indices),
            len(verified_facts),
            len(enriched_answer),
            len(final_answer),
            latency_ms,
        )

        return StepOutput(
            raw=final_answer,
            step_id=step.id,
            latency_ms=latency_ms,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
        )

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


def _emit_event(
    context: PipelineContext,
    step: StepConfig,
    total_facts: int,
    missing_indices: list[int],
    re_enriched: bool,
    latency_ms: float,
) -> None:
    """Emit observability event for enrich review."""
    if context.recorder:
        context.recorder.emit(
            EnrichReviewCompleted(
                step_name=step.id,
                total_facts=total_facts,
                missing_count=len(missing_indices),
                missing_indices=missing_indices,
                re_enriched=re_enriched,
                latency_ms=latency_ms,
            )
        )


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
