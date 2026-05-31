"""
Drop classification handler — classifies uncited verified facts post-synthesis.

Receives the combined synthesized answer and the list of uncited fact indices
(excluded_without_reason from synthesize_answer), then asks the model to
classify each one using the full answer as context.

Splitting this from synthesis gives the model the full combined prose
(all batches merged) rather than a single batch's partial view, which
makes cross-batch redundancy detectable and eliminates the need for the
synthesis model to simultaneously write prose and audit its own coverage.

Invariant: ∀ index ∈ uncited_indices: index ∈ output.excluded_with_reason
Postcondition: output.excluded_without_reason = []
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ._lib._text_utils import get_statement_text

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


def _fact_text(verified_facts: list[dict[str, Any]], index: int) -> str:
    """Return fact text for 1-based global index, or a placeholder on miss."""
    pos = index - 1
    if 0 <= pos < len(verified_facts):
        text = get_statement_text(verified_facts[pos])
        if text:
            return text
    return f"[fact {index} not found]"


def _format_uncited(
    verified_facts: list[dict[str, Any]],
    uncited_indices: list[int],
) -> str:
    lines = [f"[{idx}] {_fact_text(verified_facts, idx)}" for idx in uncited_indices]
    return "\n".join(lines)


def _parse_drops(raw: str, expected: set[int]) -> dict[int, str]:
    """Extract drop classifications from JSON response, defaulting missing indices."""
    drops: dict[int, str] = {}
    try:
        data = json.loads(raw)
        raw_drops = data.get("drops", {})
        for key, reason in raw_drops.items():
            try:
                idx = int(str(key))
                if idx in expected:
                    drops[idx] = str(reason).strip()
            except (ValueError, TypeError):
                logger.warning("classify_drops: unexpected key %r in response", key)
    except (json.JSONDecodeError, AttributeError):
        logger.warning("classify_drops: failed to parse JSON response")

    # Any expected index missing from model response → silent drop
    for idx in expected:
        if idx not in drops:
            logger.warning(
                "classify_drops: index %d absent from model response, defaulting to 'silent drop'",
                idx,
            )
            drops[idx] = "silent drop"
    return drops


class ClassifyDropsHandler(BaseHandler):
    """Classify uncited facts using the full synthesized answer as context.

    Short-circuits with empty output when no uncited facts are present,
    adding zero latency to runs that achieve full coverage.
    """

    step_type: str = "consensus_classify_drops_v8_0"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        start_time = time.monotonic()
        resolver = NamespaceResolver(context)
        inputs = step.handler_inputs or {}

        verified_facts: list[dict[str, Any]] = list(
            self._resolve_input(resolver, step, "verified_facts", inputs) or []
        )
        uncited_indices: list[int] = sorted(
            int(i)
            for i in (
                self._resolve_input(resolver, step, "uncited_indices", inputs) or []
            )
        )
        answer: str = str(self._resolve_input(resolver, step, "answer", inputs) or "")

        if not uncited_indices:
            latency_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                "Step '%s': no uncited facts — skipping classification", step.id
            )
            return StepOutput(
                raw="",
                json={
                    "excluded_with_reason": {},
                    "excluded_without_reason": [],
                },
                step_id=step.id,
                latency_ms=latency_ms,
            )

        if not step.prompt_ref:
            raise ValueError(f"Step '{step.id}': requires 'prompt_ref'")

        model_id = self._resolve_model_alias(step.model_ref or "", context)
        uncited_str = _format_uncited(verified_facts, uncited_indices)

        rendered = self._render_prompt(
            step.prompt_ref,
            {"answer": answer, "uncited_facts": uncited_str},
            context,
        )

        result = await self._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            rendered.system_prompt,
            model_id_is_resolved=True,
        )

        drops = _parse_drops(result.content.strip(), set(uncited_indices))
        latency_ms = (time.monotonic() - start_time) * 1000

        logger.info(
            "Step '%s': classified %d uncited fact(s): %s (%.0fms)",
            step.id,
            len(drops),
            {k: v for k, v in sorted(drops.items())},
            latency_ms,
        )

        return StepOutput(
            raw=result.content.strip(),
            json={
                "excluded_with_reason": drops,
                "excluded_without_reason": [],
            },
            step_id=step.id,
            latency_ms=latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            model_call_count=1,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        inputs = step.handler_inputs or {}
        for field in ("verified_facts", "uncited_indices", "answer"):
            if field not in inputs:
                errors.append(
                    f"Step '{step.id}': consensus_classify_drops_v8_0 requires "
                    f"'{field}' in handler_inputs"
                )
        if not step.prompt_ref:
            errors.append(
                f"Step '{step.id}': consensus_classify_drops_v8_0 requires 'prompt_ref'"
            )
        return errors
