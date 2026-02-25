"""
Multi-model detect → fix cycle for contamination and injection after combine.

Cycles through a model pool: each round runs audit_combined (contamination +
injection check), then if not clean runs strip_rejected with the same model.
Exits when a model reports clean or max_purge_rounds exhausted.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from .shared._chain_utils import strip_json_fences
from .shared._text_utils import get_statement_text

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_AUDIT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "contaminated": {
            "type": "array",
            "items": {"type": "string"},
        },
        "injected": {
            "type": "array",
            "items": {"type": "string"},
        },
        "clean": {"type": "boolean"},
    },
    "required": ["contaminated", "injected", "clean"],
    "additionalProperties": False,
}


def _format_bullet_list(items: list[dict[str, Any]], text_key: str = "text") -> str:
    """Format list of dicts with text_key as a bulleted list for prompts."""
    texts = [c.get(text_key, "") for c in items if c.get(text_key)]
    return "\n".join(f"- {t}" for t in texts) if texts else ""


def _resolve_purge_pool(step: StepConfig, context: PipelineContext) -> list[str]:
    """Resolve purge_pool domain field to a list of model aliases (same semantics as model_pool)."""
    pool = step.get_domain_field("purge_pool")
    if pool is None:
        return []
    if isinstance(pool, list):
        return list(pool)
    if isinstance(pool, str):
        key = pool.removeprefix("optionsNs.")
        resolved = (context.options or {}).get(key, [])
        if not isinstance(resolved, list):
            logger.error(
                "Step '%s': purge_pool option '%s' is not a list: %r",
                step.id,
                key,
                resolved,
            )
            return []
        return list(resolved)
    logger.error("Step '%s': unexpected purge_pool type: %r", step.id, pool)
    return []


class PurgeRejectedHandler(BaseHandler):
    """Multi-model detect → fix cycle for contamination and injection."""

    step_type: str = "consensus_purge_rejected_v7"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Run audit then strip in a loop until clean or max rounds."""
        start_time = time.time()
        resolver = NamespaceResolver(context)
        hi = step.handler_inputs or {}

        combined_answer = str(
            self._resolve_input(resolver, step, "combined_answer", hi) or ""
        )
        rejected_claims: list[dict[str, Any]] = (
            self._resolve_input(resolver, step, "rejected_claims", hi) or []
        )
        verified_facts: list[dict[str, Any]] = (
            self._resolve_input(resolver, step, "verified_facts", hi) or []
        )
        question = str(self._resolve_input(resolver, step, "question", hi) or "")

        if not rejected_claims:
            latency_ms = (time.time() - start_time) * 1000
            logger.info(
                "Step '%s': zero rejected claims — passing through combined answer (%d chars, %.0fms)",
                step.id,
                len(combined_answer),
                latency_ms,
            )
            return StepOutput(
                raw=combined_answer,
                step_id=step.id,
                latency_ms=latency_ms,
                json={
                    "purge_rounds": 0,
                    "clean": True,
                    "contaminated_count": 0,
                    "injected_count": 0,
                },
            )

        prompt_ref_detect = step.get_domain_field("prompt_ref_detect")
        prompt_ref_strip = step.get_domain_field("prompt_ref_strip")
        if not prompt_ref_detect or not prompt_ref_strip:
            raise ValueError(
                f"Step '{step.id}' missing prompt_ref_detect or prompt_ref_strip"
            )
        pool = _resolve_purge_pool(step, context)
        if not pool:
            raise ValueError(f"Step '{step.id}' purge_pool is empty")
        max_rounds = step.get_domain_field("max_purge_rounds")
        max_rounds = int(max_rounds) if max_rounds is not None else 3
        gen_params = step.generation_parameters or {}
        temperature = gen_params.get("temperature", 0.1)

        rejected_list = _format_bullet_list(rejected_claims)
        verified_list = "\n".join(
            f"- {get_statement_text(f)}"
            for f in verified_facts
            if get_statement_text(f)
        )
        if not verified_list:
            verified_list = "(none)"

        current_answer = combined_answer
        last_contaminated: list[str] = []
        last_injected: list[str] = []
        rounds = 0

        for round_idx in range(max_rounds):
            rounds += 1
            model_alias = pool[round_idx % len(pool)]
            model_id = self._resolve_model_alias(model_alias, context)

            # Detect: audit_combined
            detect_rendered = self._render_prompt(
                prompt_ref_detect,
                {
                    "rejected_claims_list": rejected_list,
                    "verified_facts_list": verified_list,
                    "question": question,
                    "combined_answer": current_answer,
                },
                context,
                safe=True,
            )
            max_tokens = self._resolve_max_tokens(step, context, handler_default=4096)
            call_result = await self._call_model(
                model_id,
                detect_rendered.user_prompt,
                step,
                context,
                system_prompt=detect_rendered.system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                json_schema=_AUDIT_JSON_SCHEMA,
                call_label="audit_combined",
            )
            raw = strip_json_fences(call_result.content)
            try:
                audit = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.warning(
                    "Step '%s' round %d: audit JSON parse failed: %s",
                    step.id,
                    rounds,
                    e,
                )
                break
            contaminated: list[str] = audit.get("contaminated") or []
            injected: list[str] = audit.get("injected") or []
            clean = bool(audit.get("clean", False))
            last_contaminated = contaminated
            last_injected = injected

            if clean:
                logger.info(
                    "Step '%s': clean at round %d (model %s)",
                    step.id,
                    rounds,
                    model_alias,
                )
                break

            # Fix: strip_rejected with contaminated + injected as removal targets
            removal_targets = [{"text": t} for t in contaminated + injected]
            strip_rejected_list = _format_bullet_list(removal_targets)
            strip_rendered = self._render_prompt(
                prompt_ref_strip,
                {
                    "rejected_claims_list": strip_rejected_list,
                    "question": question,
                    "original_answer": current_answer,
                },
                context,
                safe=True,
            )
            word_count = len(current_answer.split())
            dynamic_budget = max(4096, word_count * 2)
            strip_max_tokens = self._resolve_max_tokens(
                step, context, handler_default=dynamic_budget
            )
            strip_result = await self._call_model(
                model_id,
                strip_rendered.user_prompt,
                step,
                context,
                system_prompt=strip_rendered.system_prompt,
                temperature=temperature,
                max_tokens=strip_max_tokens,
                call_label="strip_rejected",
            )
            current_answer = strip_result.content.strip()

        latency_ms = (time.time() - start_time) * 1000
        return StepOutput(
            raw=current_answer,
            step_id=step.id,
            latency_ms=latency_ms,
            json={
                "purge_rounds": rounds,
                "clean": len(last_contaminated) == 0 and len(last_injected) == 0,
                "contaminated_count": len(last_contaminated),
                "injected_count": len(last_injected),
            },
        )
