"""
Post-processing handler — synthesize or pass through enriched answer.

When verification rejected zero claims, the enriched answer is already
factually clean — skip the LLM call and pass it through directly.
When rejections exist, synthesize a fresh answer from verified facts only.

Contract:
    Inputs: verified_facts, rejected_claims, enriched_answer, question
    Outputs: raw (final answer), json (metadata incl. mode)

Configuration:
    allow_passthrough (step-level or pipeline options.post_process_allow_passthrough):
        - true (default): Skip synthesis when rejected_claims is empty
        - false: Always synthesize, even with zero rejections

    Resolution order: step.allow_passthrough > options.post_process_allow_passthrough > true
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from .shared._chain_post_process import post_process_synthesize

# Matches "[N]" fact-reference markers that LLMs leak despite prompt instructions.
# Handles optional leading space to avoid orphaned whitespace.
_FACT_REF_RE = re.compile(r" ?\[\d{1,3}\]")


def _strip_fact_references(text: str) -> str:
    """Remove leaked ``[N]`` fact-reference markers from LLM output."""
    return _FACT_REF_RE.sub("", text)


if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class PostProcessHandler(BaseHandler):
    """
    Produce final answer from verification results.

    Zero rejected claims → pass through enriched answer (no LLM call).
    Non-zero rejected claims → synthesize from verified facts only.

    Output is always `.raw` — downstream consumers (reseed, output_gate)
    are agnostic to which path was taken.
    """

    step_type: str = "consensus_post_process_v4"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Synthesize or pass through depending on rejection count."""
        start_time = time.time()

        resolver = NamespaceResolver(context)
        verified_facts: list[dict[str, Any]] = (
            self._resolve_input(resolver, step, "verified_facts", step.handler_inputs)
            or []
        )
        rejected_claims: list[dict[str, Any]] = (
            self._resolve_input(resolver, step, "rejected_claims", step.handler_inputs)
            or []
        )
        hi = step.handler_inputs or {}
        enriched_answer = (
            str(self._resolve_input(resolver, step, "enriched_answer", hi) or "")
            if "enriched_answer" in hi
            else ""
        )
        question = str(
            self._resolve_input(resolver, step, "question", step.handler_inputs) or ""
        )

        if not verified_facts:
            logger.error("Step '%s': empty verified_facts input", step.id)
            return StepOutput(raw="", json={"error": "empty verified_facts"})

        # Passthrough: skip synthesis when enrichment is clean.
        # Controlled by step-level or pipeline-level `allow_passthrough` (default: true).
        allow_passthrough = step.get_domain_field("allow_passthrough")
        if allow_passthrough is None:
            allow_passthrough = (context.options or {}).get(
                "post_process_allow_passthrough", True
            )

        if allow_passthrough and not rejected_claims and enriched_answer:
            cleaned = _strip_fact_references(enriched_answer)
            latency_ms = (time.time() - start_time) * 1000
            logger.info(
                "Step '%s': zero rejected claims — passing through enriched answer "
                "(%d chars, %.0fms)",
                step.id,
                len(cleaned),
                latency_ms,
            )
            return StepOutput(
                raw=cleaned,
                json={"mode": "passthrough", "reason": "zero_rejections"},
                step_id=step.id,
                latency_ms=latency_ms,
            )

        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")
        model_id = self._resolve_model_alias(step.model_ref, context)

        prompt_ref_synthesize = str(
            step.get_domain_field("prompt_ref_synthesize") or ""
        )
        if not prompt_ref_synthesize:
            raise ValueError(f"Step '{step.id}' missing prompt_ref_synthesize")

        original_answer = (
            str(self._resolve_input(resolver, step, "original_answer", hi) or "")
            if "original_answer" in hi
            else ""
        )

        logger.info(
            "Step '%s': %d rejected claims — synthesizing from %d verified facts",
            step.id,
            len(rejected_claims),
            len(verified_facts),
        )

        raw = await post_process_synthesize(
            handler=self,
            accepted_facts=verified_facts,
            question=question,
            model_id=model_id,
            step=step,
            context=context,
            prompt_ref=prompt_ref_synthesize,
            original_answer=original_answer,
            rejected_claims=rejected_claims,
        )
        raw = _strip_fact_references(raw)

        latency_ms = (time.time() - start_time) * 1000
        logger.info(
            "Step '%s': synthesized %d chars from %d facts (%.0fms)",
            step.id,
            len(raw),
            len(verified_facts),
            latency_ms,
        )

        return StepOutput(
            raw=raw,
            json={
                "mode": "synthesize",
                "rejected_count": len(rejected_claims),
            },
            step_id=step.id,
            latency_ms=latency_ms,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        """Validate step configuration at load time."""
        errors: list[str] = []
        if not step.model_ref:
            errors.append(f"Step '{step.id}' missing model_ref")
        hi = step.handler_inputs or {}
        if "verified_facts" not in hi:
            errors.append(
                f"Step '{step.id}' missing 'verified_facts' in handler_inputs"
            )
        if "question" not in hi:
            errors.append(f"Step '{step.id}' missing 'question' in handler_inputs")
        return errors
