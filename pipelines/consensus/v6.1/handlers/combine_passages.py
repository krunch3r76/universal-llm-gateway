"""
Merge N stripped passages into a single coherent answer.

Facts-primary architecture: the verified_facts list is the sole truth source.
The 3 stripped passages serve as style/structure scaffolds only. This prevents
the model from re-injecting rejected claims from parametric knowledge.

On finish_reason == "length", retries with 2× token budget.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_MIN_COMBINE_TOKENS = 4096
_TOKENS_PER_FACT = 80


def _format_numbered_facts(verified_facts: list[dict[str, Any]]) -> str:
    """Format verified facts as a numbered list for the prompt."""
    return "\n".join(
        f"[{i}] {fact.get('text', '')}"
        for i, fact in enumerate(verified_facts, start=1)
        if fact.get("text")
    )


class CombinePassagesHandler(BaseHandler):
    """Combine 3 stripped passages into one answer, constrained to verified facts."""

    step_type: str = "consensus_combine_passages_v6_1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Combine passages using verified_facts as truth source; retry on truncation."""
        start_time = time.time()
        resolver = NamespaceResolver(context)
        hi = step.handler_inputs or {}

        passage_0 = str(self._resolve_input(resolver, step, "passage_0", hi) or "")
        passage_1 = str(self._resolve_input(resolver, step, "passage_1", hi) or "")
        passage_2 = str(self._resolve_input(resolver, step, "passage_2", hi) or "")
        question = str(self._resolve_input(resolver, step, "question", hi) or "")
        verified_facts: list[dict[str, Any]] = (
            self._resolve_input(resolver, step, "verified_facts", hi) or []
        )

        if not verified_facts:
            raise ValueError(
                f"Step '{step.id}': verified_facts is empty — cannot combine"
            )

        passages = [passage_0, passage_1, passage_2]
        non_empty = [p for p in passages if p.strip()]
        if not non_empty:
            raise ValueError(
                f"Step '{step.id}': at least one non-empty passage required"
            )
        for i, p in enumerate(passages):
            if not p.strip():
                logger.warning(
                    "Step '%s': passage_%d is empty; combining with available passages",
                    step.id,
                    i,
                )

        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")
        prompt_ref = step.get_domain_field("prompt_ref") or step.prompt_ref
        if not prompt_ref:
            raise ValueError(f"Step '{step.id}' missing prompt_ref")

        numbered_facts = _format_numbered_facts(verified_facts)
        fact_count = len([f for f in verified_facts if f.get("text")])

        dynamic_budget = max(
            _MIN_COMBINE_TOKENS,
            _TOKENS_PER_FACT * fact_count,
        )
        max_tokens = self._resolve_max_tokens(
            step, context, handler_default=dynamic_budget
        )

        rendered = self._render_prompt(
            prompt_ref,
            {
                "passage_0": passage_0,
                "passage_1": passage_1,
                "passage_2": passage_2,
                "question": question,
                "numbered_facts": numbered_facts,
                "fact_count": str(fact_count),
            },
            context,
            safe=True,
        )

        model_id = self._resolve_model_alias(step.model_ref, context)
        gen_params = step.generation_parameters or {}
        call_result = await self._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            system_prompt=rendered.system_prompt,
            temperature=gen_params.get("temperature", 0.3),
            max_tokens=max_tokens,
        )

        if call_result.finish_reason == "length":
            logger.warning(
                "Step '%s': combine truncated at %s tokens — retrying with 2× budget",
                step.id,
                call_result.completion_tokens,
            )
            retry_budget = (call_result.completion_tokens or dynamic_budget) * 2
            call_result = await self._call_model(
                model_id,
                rendered.user_prompt,
                step,
                context,
                system_prompt=rendered.system_prompt,
                temperature=gen_params.get("temperature", 0.3),
                max_tokens=retry_budget,
            )
            if call_result.finish_reason == "length":
                logger.warning(
                    "Step '%s': combine still truncated after retry (%s tokens). "
                    "Answer may be incomplete.",
                    step.id,
                    call_result.completion_tokens,
                )

        combined = call_result.content.strip()
        latency_ms = (time.time() - start_time) * 1000
        return StepOutput(
            raw=combined,
            step_id=step.id,
            latency_ms=latency_ms,
        )
