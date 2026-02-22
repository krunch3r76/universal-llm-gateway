"""
Filter universal negatives from verified facts via dedicated LLM classification.

Single-concern step: asks one model "which of these claims are universal
negatives?" and removes them. Universal negatives are claims asserting that
a subject has NO properties, significance, or relevance across an entire
domain (e.g., "42 has no mathematical significance"). These are unfalsifiable
and poison synthesis when they contradict verified positive facts.

Placed between synergize and veto_pass so negatives are removed before
any further processing.

Contract:
    Inputs: verified_facts, authority_verdicts, question
    Outputs: json.verified_facts (filtered), json.authority_verdicts (passthrough)
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from .shared._chain_utils import format_numbered_facts

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

# Maximum character length for an atomic claim. Claims exceeding this are dropped
# as they likely indicate decomposition failure or generation error.
_MAX_CLAIM_LENGTH = 800

_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "negative_indices": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
        },
    },
    "required": ["negative_indices"],
    "additionalProperties": False,
}


class FilterNegativesHandler(BaseHandler):
    """Remove universal negatives from verified facts via LLM classification."""

    step_type: str = "consensus_filter_negatives_v5_0"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Classify and remove universal negatives from verified facts."""
        start_time = time.time()
        resolver = NamespaceResolver(context)

        # 1. Resolve inputs
        verified_facts: list[dict[str, Any]] = (
            self._resolve_input(resolver, step, "verified_facts", step.handler_inputs)
            or []
        )
        authority_verdicts: dict[str, Any] = (
            self._resolve_input(
                resolver, step, "authority_verdicts", step.handler_inputs
            )
            or {}
        )
        question = str(
            self._resolve_input(resolver, step, "question", step.handler_inputs) or ""
        )

        if not verified_facts:
            return StepOutput(
                raw="",
                json={"verified_facts": [], "authority_verdicts": authority_verdicts},
                step_id=step.id,
            )

        # 2. Filter out pathologically long claims
        length_filtered_facts: list[dict[str, Any]] = []
        overlong_claims: list[str] = []
        for fact in verified_facts:
            text = fact.get("text", str(fact))
            if len(text) > _MAX_CLAIM_LENGTH:
                preview = text[:100] + "..." if len(text) > 100 else text
                overlong_claims.append(preview)
                logger.warning(
                    "Dropping overlong claim (%d chars, limit %d): %s",
                    len(text),
                    _MAX_CLAIM_LENGTH,
                    preview,
                )
            else:
                length_filtered_facts.append(fact)

        if overlong_claims:
            logger.warning(
                "Dropped %d overlong claim(s) before negative filtering",
                len(overlong_claims),
            )

        if not length_filtered_facts:
            return StepOutput(
                raw="",
                json={"verified_facts": [], "authority_verdicts": authority_verdicts},
                step_id=step.id,
            )

        # 3. Format facts as numbered list for prompt (0-based indices for negative_indices)
        numbered_facts = format_numbered_facts(length_filtered_facts, start_index=0)

        # 4. Render prompt and call model
        prompt_ref = step.prompt_ref
        if not prompt_ref:
            raise ValueError(f"Step '{step.id}' missing prompt_ref")

        rendered = self._render_prompt(
            prompt_ref,
            {"numbered_facts": numbered_facts, "question": question},
            context,
            safe=True,
        )

        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")
        model_id = self._resolve_model_alias(step.model_ref, context)

        call_result = await self._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            system_prompt=rendered.system_prompt,
            temperature=0.0,
            max_tokens=self._resolve_max_tokens(step, context, handler_default=512),
            json_schema=_JSON_SCHEMA,
        )

        # 5. Parse response and filter
        negative_indices = _parse_negative_indices(
            call_result.content, len(length_filtered_facts)
        )
        negative_indices_set = set(negative_indices)

        filtered_facts: list[dict[str, Any]] = []
        removed_texts: list[str] = []
        for idx, fact in enumerate(length_filtered_facts):
            if idx in negative_indices_set:
                removed_texts.append(fact.get("text", str(fact)))
            else:
                filtered_facts.append(fact)

        latency_ms = (time.time() - start_time) * 1000

        if removed_texts:
            logger.info(
                "Step '%s': removed %d universal negative(s) from %d facts (%.0fms): %s",
                step.id,
                len(removed_texts),
                len(length_filtered_facts),
                latency_ms,
                removed_texts,
            )
        else:
            logger.info(
                "Step '%s': no universal negatives found in %d facts (%.0fms)",
                step.id,
                len(length_filtered_facts),
                latency_ms,
            )

        return StepOutput(
            raw="",
            json={
                "verified_facts": filtered_facts,
                "authority_verdicts": authority_verdicts,
            },
            step_id=step.id,
            latency_ms=latency_ms,
            prompt_tokens=call_result.prompt_tokens,
            completion_tokens=call_result.completion_tokens,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        """Validate step configuration."""
        errors: list[str] = []
        if not step.model_ref:
            errors.append(f"Step '{step.id}' missing model_ref")
        if not step.prompt_ref:
            errors.append(f"Step '{step.id}' missing prompt_ref")
        inputs = step.handler_inputs or {}
        if "verified_facts" not in inputs:
            errors.append(
                f"Step '{step.id}' missing 'verified_facts' in handler_inputs"
            )
        return errors


def _parse_negative_indices(raw_response: str, fact_count: int) -> list[int]:
    """Parse LLM response into validated list of negative indices."""
    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse filter_negatives response: %s", e)
        return []

    indices = data.get("negative_indices", [])
    if not isinstance(indices, list):
        logger.warning("negative_indices is not a list: %s", type(indices))
        return []

    valid: list[int] = []
    for idx in indices:
        if isinstance(idx, int) and 0 <= idx < fact_count:
            valid.append(idx)
        else:
            logger.warning(
                "Ignoring out-of-range negative index: %s (max=%d)", idx, fact_count - 1
            )
    return valid
