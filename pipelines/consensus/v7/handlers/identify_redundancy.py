"""
Redundancy identification — scans the batched synthesis draft for
semantically duplicate claims expressed across independently-written sections.

Each synthesize_answer batch covers a disjoint fact window but cannot see what
other batches have written, so each naturally introduces the same topic (e.g.
mechanism of action) from its local fact set. This step finds cross-section
duplicates and outputs a merge plan for reorganize_answer to execute.

When batch_texts is provided (list of per-batch prose), the draft is composed
as labeled sections (=== SECTION N ===) so the model can distinguish within-
batch co-citations from genuine cross-batch duplicates. Falls back to the flat
combined answer if batch_texts is absent or empty.

Output:
    merge_groups: list[{theme: str, citations: list[int]}]
        Each group = one logical claim expressed by ≥ 2 distinct sentences
        from different sections.
        citations = all [N] indices from all the duplicate sentences combined.

∀ group G: |G.citations| ≥ 2
Fallback on parse failure: {"merge_groups": []}
"""

from __future__ import annotations

import json
import re
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

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    # qwen wraps JSON output in {{ }} (Python f-string style) — strip outer double braces
    stripped = text.strip()
    while stripped.startswith("{{") and stripped.endswith("}}"):
        stripped = stripped[1:-1].strip()
    match = _JSON_RE.search(stripped)
    if not match:
        return {}
    return json.loads(match.group())


def _compose_draft(answer: str, batch_texts: list[str]) -> str:
    """Compose the draft with explicit section markers when batch texts are available.

    ∀ batch_texts non-empty: draft = labeled sections so the model can enforce
    cross-section-only detection. Falls back to the flat answer otherwise.
    """
    valid = [t.strip() for t in batch_texts if t.strip()]
    if not valid:
        return answer
    sections = "\n\n".join(
        f"=== SECTION {i + 1} ===\n{text}" for i, text in enumerate(valid)
    )
    return sections


def _parse_merge_groups(raw: str) -> list[dict[str, Any]]:
    parsed = _extract_json(raw)
    raw_groups = parsed.get("merge_groups", [])
    if not isinstance(raw_groups, list):
        return []
    groups: list[dict[str, Any]] = []
    for g in raw_groups:
        if not isinstance(g, dict) or "citations" not in g:
            continue
        seen: set[int] = set()
        citations: list[int] = []
        for c in g["citations"]:
            try:
                n = int(c)
                if n not in seen:
                    seen.add(n)
                    citations.append(n)
            except (ValueError, TypeError):
                pass
        if len(citations) >= 2:
            groups.append(
                {"theme": str(g.get("theme", "")), "citations": sorted(citations)}
            )
    return groups


class IdentifyRedundancyHandler(BaseHandler):
    """Identify semantically redundant claims across batched synthesis sections."""

    step_type: str = "consensus_identify_redundancy_v7"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        start_time = time.monotonic()
        resolver = NamespaceResolver(context)
        inputs = step.handler_inputs or {}

        answer: str = str(self._resolve_input(resolver, step, "answer", inputs))
        question: str = str(self._resolve_input(resolver, step, "question", inputs))
        raw_batch_texts = (
            self._resolve_input(resolver, step, "batch_texts", inputs)
            if "batch_texts" in inputs
            else None
        )
        batch_texts: list[str] = (
            raw_batch_texts if isinstance(raw_batch_texts, list) else []
        )

        def _empty(reason: str) -> StepOutput:
            latency = (time.monotonic() - start_time) * 1000
            logger.info("Step '%s': skipping identification — %s", step.id, reason)
            return StepOutput(
                raw="",
                json={"merge_groups": []},
                step_id=step.id,
                latency_ms=latency,
            )

        if not answer.strip():
            return _empty("answer is empty")

        if not step.prompt_ref:
            logger.error("Step '%s': prompt_ref is required", step.id)
            return _empty("prompt_ref not configured")

        sys_ref: str | None = step.get_domain_field("system_prompt_ref")
        model_id = self._resolve_model_alias(step.model_ref or "", context)

        cached_sys: str | None = None
        if sys_ref:
            cached_sys = self._render_prompt(sys_ref, {}, context).user_prompt

        draft = _compose_draft(answer, batch_texts)
        using_sections = bool(batch_texts)
        logger.info(
            "Step '%s': composing draft as %s",
            step.id,
            f"{len(batch_texts)} labeled sections" if using_sections else "flat answer",
        )

        rendered = self._render_prompt(
            step.prompt_ref, {"question": question, "answer": draft}, context
        )

        result = await self._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            cached_sys or rendered.system_prompt,
            model_id_is_resolved=True,
        )

        raw = result.content.strip()
        merge_groups: list[dict[str, Any]] = []

        try:
            merge_groups = _parse_merge_groups(raw)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("Step '%s': failed to parse merge groups — %s", step.id, exc)

        latency_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "Step '%s': identified %d merge group(s) (%.0fms)",
            step.id,
            len(merge_groups),
            latency_ms,
        )

        return StepOutput(
            raw=raw,
            json={"merge_groups": merge_groups},
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
        for field in ("answer", "question"):
            if field not in inputs:
                errors.append(
                    f"Step '{step.id}': consensus_identify_redundancy_v7 requires '{field}' "
                    "in handler_inputs"
                )
        if not step.prompt_ref:
            errors.append(
                f"Step '{step.id}': consensus_identify_redundancy_v7 requires 'prompt_ref'"
            )
        return errors
