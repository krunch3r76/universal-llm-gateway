"""
Batched synthesis handler — synthesize facts in small batches to stay
within a 7B model's citation tracking capacity.

Reads fact_clusters from group_facts, merges small adjacent clusters
into batches ≤ max_batch_size, calls the synthesis prompt per batch
with original global indices, then concatenates prose and aggregates
citation coverage stats.

Drop classification is intentionally excluded from this step. The model
only writes prose with inline citations. A downstream classify_drops step
classifies any uncited facts using the full combined prose as context,
which is more accurate than per-batch classification where cross-batch
coverage is invisible.
"""

from __future__ import annotations

import re
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

_BRACKET_RE = re.compile(r"\[[\d,\s]+\]")
_DEFAULT_MAX_BATCH_SIZE = 15


def _merge_clusters_into_batches(
    clusters: list[list[dict[str, Any]]],
    max_size: int,
) -> list[list[dict[str, Any]]]:
    """Merge adjacent clusters into batches of at most max_size facts.

    Walks clusters in order; appends each cluster to the current batch
    if it fits, otherwise starts a new batch. Clusters that individually
    exceed max_size are split into consecutive sub-batches of max_size.
    """
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for cluster in clusters:
        if len(cluster) > max_size:
            if current:
                batches.append(current)
                current = []
            for i in range(0, len(cluster), max_size):
                batches.append(cluster[i : i + max_size])
        else:
            if current and len(current) + len(cluster) > max_size:
                batches.append(current)
                current = []
            current.extend(cluster)
    if current:
        batches.append(current)
    return batches


def _format_batch_facts(
    facts: list[dict[str, Any]],
    global_offset: int,
) -> str:
    """Format facts as numbered lines with global 1-based indices."""
    lines: list[str] = []
    for i, fact in enumerate(facts):
        text = get_statement_text(fact)
        if text:
            lines.append(f"[{global_offset + i}] {text}")
    return "\n".join(lines)


def _extract_indices(text: str) -> set[int]:
    indices: set[int] = set()
    for bracket in _BRACKET_RE.findall(text):
        indices.update(int(n) for n in re.findall(r"\d+", bracket))
    return indices


class SynthesizeBatchedHandler(BaseHandler):
    """Synthesize answer in batches sized for reliable citation tracking.

    Outputs incorporated (cited indices) and excluded_without_reason
    (uncited indices). Drop classification is deferred to classify_drops.
    """

    step_type: str = "consensus_synthesize_batched_v8_0"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        start_time = time.monotonic()
        resolver = NamespaceResolver(context)

        fact_clusters: list[list[dict[str, Any]]] = self._resolve_input(
            resolver, step, "fact_clusters", step.handler_inputs or {}
        )
        question: str = str(
            self._resolve_input(resolver, step, "question", step.handler_inputs or {})
        )

        max_batch_size = int(
            step.get_domain_field("max_batch_size") or _DEFAULT_MAX_BATCH_SIZE
        )
        if not step.prompt_ref:
            raise ValueError(f"Step '{step.id}': requires 'prompt_ref'")
        prompt_ref: str = step.prompt_ref
        sys_ref: str | None = step.get_domain_field("system_prompt_ref")

        model_id = self._resolve_model_alias(step.model_ref or "", context)

        batches = _merge_clusters_into_batches(fact_clusters, max_batch_size)

        # Render system prompt once — instructions only, no fact data
        cached_sys: str | None = None
        if sys_ref:
            cached_sys = self._render_prompt(sys_ref, {}, context).user_prompt

        prose_parts: list[str] = []
        all_incorporated: list[int] = []
        all_excluded_without_reason: list[int] = []
        total_pt = 0
        total_ct = 0
        global_offset = 1

        for batch_idx, batch in enumerate(batches):
            batch_facts_str = _format_batch_facts(batch, global_offset)
            batch_expected = set(range(global_offset, global_offset + len(batch)))

            user_ctx: dict[str, Any] = {
                "question": question,
                "verified_facts": batch_facts_str,
            }
            rendered = self._render_prompt(prompt_ref, user_ctx, context)

            result = await self._call_model(
                model_id,
                rendered.user_prompt,
                step,
                context,
                cached_sys or rendered.system_prompt,
                call_label=f"batch_{batch_idx}",
                model_id_is_resolved=True,
            )
            total_pt += result.prompt_tokens
            total_ct += result.completion_tokens

            prose = result.content.strip()
            if prose:
                prose_parts.append(prose)

            incorporated = sorted(batch_expected & _extract_indices(prose))
            excluded_nr = sorted(batch_expected - set(incorporated))

            all_incorporated.extend(incorporated)
            all_excluded_without_reason.extend(excluded_nr)

            global_offset += len(batch)

            logger.info(
                "Step '%s' batch %d/%d: %d facts → inc=%d uncited=%d",
                step.id,
                batch_idx + 1,
                len(batches),
                len(batch),
                len(incorporated),
                len(excluded_nr),
            )

        combined_prose = "\n\n".join(prose_parts)
        latency_ms = (time.monotonic() - start_time) * 1000

        logger.info(
            "Step '%s': %d batches, %d facts → inc=%d uncited=%d (%.0fms)",
            step.id,
            len(batches),
            sum(len(b) for b in batches),
            len(all_incorporated),
            len(all_excluded_without_reason),
            latency_ms,
        )

        valid_prose = [t.strip() for t in prose_parts if t.strip()]
        sectioned_draft = (
            "\n\n".join(
                f"=== SECTION {i + 1} ===\n{text}" for i, text in enumerate(valid_prose)
            )
            if valid_prose
            else combined_prose
        )

        return StepOutput(
            raw=combined_prose,
            json={
                "answer": combined_prose,
                "sectioned_draft": sectioned_draft,
                "batch_texts": prose_parts,
                "incorporated": sorted(all_incorporated),
                "excluded_without_reason": sorted(all_excluded_without_reason),
                "batch_count": len(batches),
                "batch_sizes": [len(b) for b in batches],
            },
            step_id=step.id,
            latency_ms=latency_ms,
            prompt_tokens=total_pt,
            completion_tokens=total_ct,
            model_call_count=len(batches),
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        inputs = step.handler_inputs or {}
        if "fact_clusters" not in inputs:
            errors.append(
                f"Step '{step.id}': requires 'fact_clusters' in handler_inputs"
            )
        if "question" not in inputs:
            errors.append(f"Step '{step.id}': requires 'question' in handler_inputs")
        if not step.prompt_ref:
            errors.append(f"Step '{step.id}': requires 'prompt_ref'")
        return errors
