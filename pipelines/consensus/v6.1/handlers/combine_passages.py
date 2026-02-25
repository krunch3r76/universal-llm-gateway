"""Merge stripped passages into a verified-facts-constrained answer."""

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

_MIN_COMBINE_TOKENS = 8192
_TOKENS_PER_FACT = 80
# Maximum facts per synthesis call. Small models (<=10B) lose coherence when asked
# to track >~40 facts simultaneously while also formatting structured output from 3
# long passages. Chunking caps the working set; subsequent chunk calls use the
# revise_synthesis prompt to weave each batch into the accumulating answer.
_DEFAULT_SYNTHESIS_CHUNK_SIZE = 0  # 0 = disabled (single-call path, original behaviour)


def _format_numbered_facts(verified_facts: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"[{i}] {fact.get('text', '')}"
        for i, fact in enumerate(verified_facts, start=1)
        if fact.get("text")
    )


class CombinePassagesHandler(BaseHandler):
    step_type: str = "consensus_combine_passages_v6_1"

    async def _single_synthesis(
        self,
        step: StepConfig,
        context: PipelineContext,
        verified_facts: list[dict[str, Any]],
        passages: tuple[str, str, str],
        question: str,
    ) -> str:
        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")

        prompt_ref = step.get_domain_field("prompt_ref") or step.prompt_ref
        if not prompt_ref:
            raise ValueError(f"Step '{step.id}' missing prompt_ref")

        passage_0, passage_1, passage_2 = passages
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
                    "Step '%s': combine still truncated after retry (%s tokens). Answer may be incomplete.",
                    step.id,
                    call_result.completion_tokens,
                )

        return call_result.content.strip()

    async def _chunked_synthesis(
        self,
        step: StepConfig,
        context: PipelineContext,
        verified_facts: list[dict[str, Any]],
        passages: tuple[str, str, str],
        question: str,
        chunk_size: int,
    ) -> str:
        chunks = [
            verified_facts[i : i + chunk_size]
            for i in range(0, len(verified_facts), chunk_size)
        ]
        logger.info(
            "Step '%s': chunked synthesis — %d facts in %d chunks of <=%d",
            step.id,
            len(verified_facts),
            len(chunks),
            chunk_size,
        )

        # Chunk 0: bootstrap with passages as structural scaffold.
        answer = await self._single_synthesis(
            step, context, chunks[0], passages, question
        )

        # Chunks 1+: enrich the growing answer; passages are no longer needed.
        re_enrich_ref = str(step.get_domain_field("prompt_ref_re_enrich") or "")
        if not re_enrich_ref:
            raise ValueError(
                f"Step '{step.id}': synthesis_chunk_size requires prompt_ref_re_enrich"
            )
        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")

        model_id = self._resolve_model_alias(step.model_ref, context)
        gen_params = step.generation_parameters or {}
        for chunk in chunks[1:]:
            missing_text = _format_numbered_facts(chunk)
            rendered = self._render_prompt(
                re_enrich_ref,
                {
                    "enriched_answer": answer,
                    "missing_facts": missing_text,
                    "question": question,
                },
                context,
                safe=True,
            )
            revision_budget = max(
                2048,
                _TOKENS_PER_FACT * len(answer.split()) + 200 * len(chunk),
            )
            result = await self._call_model(
                model_id,
                rendered.user_prompt,
                step,
                context,
                system_prompt=rendered.system_prompt,
                temperature=gen_params.get("temperature", 0.3),
                max_tokens=self._resolve_max_tokens(
                    step, context, handler_default=revision_budget
                ),
            )
            answer = result.content.strip()

        return answer

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
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

        passages = (passage_0, passage_1, passage_2)
        if not any(p.strip() for p in passages):
            raise ValueError(
                f"Step '{step.id}': at least one non-empty passage required"
            )
        chunk_size = int(
            step.get_domain_field("synthesis_chunk_size")
            or _DEFAULT_SYNTHESIS_CHUNK_SIZE
        )
        if chunk_size > 0 and len(verified_facts) > chunk_size:
            combined = await self._chunked_synthesis(
                step,
                context,
                verified_facts,
                passages,
                question,
                chunk_size,
            )
        else:
            combined = await self._single_synthesis(
                step,
                context,
                verified_facts,
                passages,
                question,
            )
        latency_ms = (time.time() - start_time) * 1000
        return StepOutput(
            raw=combined,
            step_id=step.id,
            latency_ms=latency_ms,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors = super().validate(step) if hasattr(super(), "validate") else []
        chunk_size = step.get_domain_field("synthesis_chunk_size")
        if chunk_size and int(chunk_size) > 0:
            if not step.get_domain_field("prompt_ref_re_enrich"):
                errors.append(
                    f"Step '{step.id}': synthesis_chunk_size requires prompt_ref_re_enrich"
                )
        return errors
