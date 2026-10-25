"""
Filter orphaned sub-claims from verified facts.

Asks one model to identify claims that lost their grammatical subject through
compound decomposition. A claim is orphaned when its subject is a vague pronoun
or indefinite determiner ("It", "These conditions", "Some factors") with no
specific referent — making the claim unfalsifiable in isolation.

Named-entity subjects are never orphaned regardless of surrounding context.
The prompt applies the named-entity exclusion rule first (Rule 1) before
checking for vague subjects (Rule 2), preventing the false-positive pattern
where context-dependent claims are incorrectly flagged.

Placed sequentially after filter_negatives so it never evaluates claims
already removed by that step.

Contract:
    Inputs: verified_facts, authority_verdicts, question
    Outputs: json.verified_facts (filtered), json.authority_verdicts (passthrough)
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ._lib._chain_utils import format_numbered_facts
from ._lib._index_utils import build_rendered_order

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

# Protects expletive "It is [adj] for [actor] to [action]" claims from being
# orphaned. The grammatical subject "It" is a placeholder — the real agent is
# the noun phrase after "for". These are complete, specific claims that the 7B
# model cannot distinguish from true orphans via prompt instruction alone.
# Pattern: starts with "It is/was/will be", contains "for [words] to [verb]".
_EXPLETIVE_IT_RE = re.compile(
    r"^[Ii]t\b.{0,30}\bfor\s+\w.{0,80}\bto\s+\w",
    re.DOTALL,
)


_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "orphaned_indices": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
        },
    },
    "required": ["orphaned_indices"],
    "additionalProperties": False,
}


class FilterOrphansHandler(BaseHandler):
    """Remove orphaned sub-claims from verified facts via LLM classification."""

    step_type: str = "consensus_filter_orphans_v8_0"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Classify and remove orphaned sub-claims from verified facts."""
        start_time = time.time()
        resolver = NamespaceResolver(context)

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

        numbered_facts = format_numbered_facts(verified_facts, start_index=0)

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

        fact_count = len(verified_facts)
        rendered_indices = _parse_indices(
            call_result.content, "orphaned_indices", fact_count
        )
        # Map rendered indices (model's view) to original list positions.
        # format_numbered_facts groups by context_prefix before numbering, so
        # rendered index N ≠ verified_facts[N] when context groups reorder facts.
        rendered_order = build_rendered_order(verified_facts)
        orphaned_original = [
            rendered_order[i] for i in rendered_indices if i < len(rendered_order)
        ]
        orphaned_set = _apply_expletive_it_guard(
            orphaned_original, verified_facts, step.id, logger
        )

        filtered_facts: list[dict[str, Any]] = []
        removed_orphans: list[str] = []
        for idx, fact in enumerate(verified_facts):
            text = fact.get("text", str(fact))
            if idx in orphaned_set:
                removed_orphans.append(text)
            else:
                filtered_facts.append(fact)

        latency_ms = (time.time() - start_time) * 1000

        if removed_orphans:
            logger.info(
                "Step '%s': removed %d orphaned sub-claim(s) (%.0fms): %s",
                step.id,
                len(removed_orphans),
                latency_ms,
                removed_orphans,
            )
        else:
            logger.info(
                "Step '%s': no orphans in %d facts (%.0fms)",
                step.id,
                fact_count,
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


def _apply_expletive_it_guard(
    orphaned_indices: list[int],
    verified_facts: list[dict[str, Any]],
    step_id: str,
    log: logging.Logger,
) -> set[int]:
    """Remove expletive-It claims from the orphaned set.

    'It is [adj] for [actor] to [action]' constructions have a named agent in
    the for-clause. The 7B model cannot distinguish these from true orphans via
    prompt instruction, so we override here.
    ∀ idx ∈ orphaned_indices: text matches _EXPLETIVE_IT_RE ⟹ idx ∉ result
    """
    protected: list[str] = []
    final: set[int] = set()
    for idx in orphaned_indices:
        text = verified_facts[idx].get("text", "") if idx < len(verified_facts) else ""
        if _EXPLETIVE_IT_RE.match(text):
            protected.append(f"[{idx}] {text}")
        else:
            final.add(idx)
    if protected:
        log.info(
            "Step '%s': expletive-It guard protected %d claim(s): %s",
            step_id,
            len(protected),
            protected,
        )
    return final


def _parse_indices(raw_response: str, key: str, fact_count: int) -> list[int]:
    """Parse a named integer-array field from the LLM JSON response.

    ∀ index i returned: 0 ≤ i < fact_count.  Out-of-range values are logged
    and dropped so a malformed response never removes valid facts.
    """
    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse filter_orphans response: %s", e)
        return []

    indices = data.get(key, [])
    if not isinstance(indices, list):
        logger.warning("%s is not a list: %s", key, type(indices))
        return []

    valid: list[int] = []
    for idx in indices:
        if isinstance(idx, int) and 0 <= idx < fact_count:
            valid.append(idx)
        else:
            logger.warning(
                "Ignoring out-of-range %s index: %s (max=%d)", key, idx, fact_count - 1
            )
    return valid
