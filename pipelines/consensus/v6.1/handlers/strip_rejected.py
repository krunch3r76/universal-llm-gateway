"""
Strip rejected claims from an original answer, preserving passage structure.

When rejected_claims is empty, returns the original answer unchanged (no LLM call).
Otherwise renders the strip_rejected prompt and returns the edited passage.
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


def _format_rejected_list(rejected_claims: list[dict[str, Any]]) -> str:
    """Format rejected claims as a bulleted list for the prompt."""
    texts = [c.get("text", "") for c in rejected_claims if c.get("text")]
    return "\n".join(f"- {t}" for t in texts) if texts else ""


class StripRejectedHandler(BaseHandler):
    """Remove rejected claims from an original answer, preserving passage structure."""

    step_type: str = "consensus_strip_rejected_v6_1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Strip rejected claims from the answer; passthrough when rejections empty."""
        start_time = time.time()
        resolver = NamespaceResolver(context)
        hi = step.handler_inputs or {}

        original_answer = str(
            self._resolve_input(resolver, step, "original_answer", hi) or ""
        )
        rejected_claims: list[dict[str, Any]] = (
            self._resolve_input(resolver, step, "rejected_claims", hi) or []
        )
        question = str(self._resolve_input(resolver, step, "question", hi) or "")

        if not rejected_claims:
            latency_ms = (time.time() - start_time) * 1000
            logger.info(
                "Step '%s': zero rejected claims — passing through original answer (%d chars, %.0fms)",
                step.id,
                len(original_answer),
                latency_ms,
            )
            return StepOutput(
                raw=original_answer,
                step_id=step.id,
                latency_ms=latency_ms,
            )

        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")
        prompt_ref = step.get_domain_field("prompt_ref") or step.prompt_ref
        if not prompt_ref:
            raise ValueError(f"Step '{step.id}' missing prompt_ref")

        rejected_list = _format_rejected_list(rejected_claims)
        rendered = self._render_prompt(
            prompt_ref,
            {
                "rejected_claims_list": rejected_list,
                "question": question,
                "original_answer": original_answer,
            },
            context,
            safe=True,
        )

        word_count = len(original_answer.split())
        dynamic_budget = max(4096, word_count * 2)
        max_tokens = self._resolve_max_tokens(
            step, context, handler_default=dynamic_budget
        )

        model_id = self._resolve_model_alias(step.model_ref, context)
        gen_params = step.generation_parameters or {}
        call_result = await self._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            system_prompt=rendered.system_prompt,
            temperature=gen_params.get("temperature", 0.2),
            max_tokens=max_tokens,
        )

        stripped = call_result.content.strip()
        latency_ms = (time.time() - start_time) * 1000
        return StepOutput(
            raw=stripped,
            step_id=step.id,
            latency_ms=latency_ms,
        )
