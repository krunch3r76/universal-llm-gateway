"""
Post-synthesis answer reorganization.

Takes the concatenated batched prose from synthesize_answer and asks the LLM
to reorganize it for thematic coherence: merging cross-batch redundancy, folding
orphan sentences, and reordering paragraphs logically.

Invariants:
    ∀ citation [N] ∈ input prose: [N] ∈ output prose  (unless fallback triggered)
    |output citations lost| / |input incorporated| ≤ 0.20  (else fall back)
    output.excluded_with_reason == input.excluded_with_reason  (passed through)
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.errors import BindingResolutionError
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_BRACKET_RE = re.compile(r"\[[\d,\s]+\]")
_MAX_CITATION_LOSS_RATIO = 0.20


def _extract_cited_indices(text: str) -> set[int]:
    """Extract all [N] and [N, M, ...] citation indices from prose."""
    indices: set[int] = set()
    for bracket in _BRACKET_RE.findall(text):
        indices.update(int(n) for n in re.findall(r"\d+", bracket))
    return indices


def _paragraph_count(text: str) -> int:
    """Count non-empty paragraphs (blank-line separated blocks)."""
    return sum(1 for block in text.split("\n\n") if block.strip())


def _format_merge_plan(merge_groups: list[dict[str, Any]]) -> str:
    """Render merge groups as explicit per-group instructions for the model."""
    if not merge_groups:
        return "No redundant claim groups identified — proceed with standard reorganization."
    lines: list[str] = []
    for i, group in enumerate(merge_groups, 1):
        theme = group.get("theme", "shared claim")
        citations = group.get("citations", [])
        cite_str = ", ".join(str(c) for c in sorted(citations))
        lines.append(f'Group {i} — "{theme}"')
        lines.append(f"  Combine citations [{cite_str}] into ONE sentence.\n")
    return "\n".join(lines)


def _deduplicate_citations(text: str) -> tuple[str, list[int]]:
    """Remove duplicate [N] index occurrences across the entire output.

    ∀ index N: appears exactly once in output at its first occurrence site.
    Returns the cleaned text and a list of removed indices.
    """
    seen: set[int] = set()
    removed: list[int] = []

    def _clean_bracket(match: re.Match[str]) -> str:
        nums = [int(n) for n in re.findall(r"\d+", match.group())]
        kept = [n for n in nums if n not in seen]
        dupes = [n for n in nums if n in seen]
        seen.update(kept)
        removed.extend(dupes)
        if not kept:
            return ""
        return "[" + ", ".join(str(n) for n in kept) + "]"

    cleaned = _BRACKET_RE.sub(_clean_bracket, text)
    cleaned = re.sub(r" {2,}", " ", cleaned)
    cleaned = re.sub(r" +([.,;:!?])", r"\1", cleaned)
    return cleaned, removed


class ReorganizeAnswerHandler(BaseHandler):
    """Reorganize batched synthesis prose into thematically coherent paragraphs."""

    step_type: str = "consensus_reorganize_v8_0"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        start_time = time.monotonic()
        resolver = NamespaceResolver(context)
        inputs = step.handler_inputs or {}

        answer: str = str(self._resolve_input(resolver, step, "answer", inputs))
        question: str = str(self._resolve_input(resolver, step, "question", inputs))
        incorporated: list[int] = list(
            self._resolve_input(resolver, step, "incorporated", inputs) or []
        )
        excluded_with_reason: dict[str, Any] = dict(
            self._resolve_input(resolver, step, "excluded_with_reason", inputs) or {}
        )
        merge_groups: list[dict[str, Any]] = []
        if "merge_groups" in inputs:
            try:
                raw_groups = self._resolve_input(resolver, step, "merge_groups", inputs)
                if isinstance(raw_groups, list):
                    merge_groups = raw_groups
            except (KeyError, ValueError, BindingResolutionError):
                # Upstream step was skipped (enabled: false) — proceed without merge plan
                logger.info(
                    "Step '%s': merge_groups unavailable (upstream skipped)", step.id
                )

        def _passthrough(reason: str) -> StepOutput:
            latency = (time.monotonic() - start_time) * 1000
            logger.info("Step '%s': skipping reorganization — %s", step.id, reason)
            original_cited = _extract_cited_indices(answer)
            excl_indices = {int(k) for k in excluded_with_reason if str(k).isdigit()}
            excluded_without_reason = sorted(
                set(incorporated) - original_cited - excl_indices
            )
            return StepOutput(
                raw=answer,
                json={
                    "answer": answer,
                    "incorporated": sorted(original_cited & set(incorporated)),
                    "excluded_with_reason": excluded_with_reason,
                    "excluded_without_reason": excluded_without_reason,
                    "reorganized": False,
                },
                step_id=step.id,
                latency_ms=latency,
            )

        if not answer.strip():
            return _passthrough("answer is empty")

        if _paragraph_count(answer) < 2:
            return _passthrough("answer has fewer than 2 paragraphs")

        if not step.prompt_ref:
            logger.error("Step '%s': prompt_ref is required", step.id)
            return _passthrough("prompt_ref not configured")

        sys_ref: str | None = step.get_domain_field("system_prompt_ref")
        model_id = self._resolve_model_alias(step.model_ref or "", context)

        cached_sys: str | None = None
        if sys_ref:
            cached_sys = self._render_prompt(sys_ref, {}, context).user_prompt

        user_ctx = {
            "question": question,
            "answer": answer,
            "merge_plan": _format_merge_plan(merge_groups),
        }
        rendered = self._render_prompt(step.prompt_ref, user_ctx, context)

        result = await self._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            cached_sys or rendered.system_prompt,
            model_id_is_resolved=True,
        )

        reorganized = result.content.strip()

        if not reorganized:
            logger.warning(
                "Step '%s': LLM returned empty output, falling back", step.id
            )
            return _passthrough("LLM returned empty output")

        reorganized, deduped = _deduplicate_citations(reorganized)
        if deduped:
            logger.info(
                "Step '%s': deduplicated %d citation occurrence(s): %s",
                step.id,
                len(deduped),
                sorted(set(deduped)),
            )

        new_cited = _extract_cited_indices(reorganized)
        original_set = set(incorporated)
        lost = original_set - new_cited
        loss_ratio = len(lost) / len(original_set) if original_set else 0.0

        if loss_ratio > _MAX_CITATION_LOSS_RATIO:
            logger.warning(
                "Step '%s': reorganization lost %d/%d citations (%.0f%% > %.0f%% threshold), "
                "falling back to original",
                step.id,
                len(lost),
                len(original_set),
                loss_ratio * 100,
                _MAX_CITATION_LOSS_RATIO * 100,
            )
            return _passthrough(f"citation loss {loss_ratio:.0%} exceeds threshold")

        if lost:
            logger.info(
                "Step '%s': reorganization dropped %d citation(s): %s",
                step.id,
                len(lost),
                sorted(lost),
            )

        excl_indices = {int(k) for k in excluded_with_reason if str(k).isdigit()}
        excluded_without_reason = sorted(original_set - new_cited - excl_indices)

        latency_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "Step '%s': reorganized %d paragraphs → %d paragraphs, "
            "citations: %d/%d retained (%.0fms)",
            step.id,
            _paragraph_count(answer),
            _paragraph_count(reorganized),
            len(new_cited & original_set),
            len(original_set),
            latency_ms,
        )

        return StepOutput(
            raw=reorganized,
            json={
                "answer": reorganized,
                "incorporated": sorted(new_cited & original_set),
                "excluded_with_reason": excluded_with_reason,
                "excluded_without_reason": excluded_without_reason,
                "reorganized": True,
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
        for field in ("answer", "question", "incorporated", "excluded_with_reason"):
            if field not in inputs:
                errors.append(
                    f"Step '{step.id}': consensus_reorganize_v8_0 requires '{field}' "
                    "in handler_inputs"
                )
        if not step.prompt_ref:
            errors.append(
                f"Step '{step.id}': consensus_reorganize_v8_0 requires 'prompt_ref'"
            )
        return errors
