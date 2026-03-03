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


def _format_sentences_for_comparison(sentences: list[dict[str, Any]]) -> str:
    """Format pre-extracted sentences as a structured list grouped by section."""
    by_section: dict[int, list[dict[str, Any]]] = {}
    for s in sentences:
        sec = int(s.get("section", 0))
        by_section.setdefault(sec, []).append(s)
    lines: list[str] = []
    for sec in sorted(by_section):
        lines.append(f"=== SECTION {sec} ===")
        for s in by_section[sec]:
            cites = s.get("citations", [])
            cite_str = " " + " ".join(f"[{c}]" for c in cites) if cites else ""
            lines.append(f"- {s.get('text', '')}{cite_str}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict[str, Any]:
    # qwen wraps every brace in {{ }} (Python f-string style) — replace all occurrences
    # globally before parsing; the while-loop approach only fixed the outermost layer,
    # leaving inner {{"key": ...}} entries invalid after one strip.
    stripped = text.strip().replace("{{", "{").replace("}}", "}")
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


def _parse_merge_groups(
    raw: str, enforce_sections: bool = False
) -> list[dict[str, Any]]:
    """Parse and validate merge groups from model output.

    ∀ group G: |G.citations| ≥ 2
    When enforce_sections=True (batch_texts provided):
        ∀ group G: len(set(G.sections)) ≥ 2 — else discarded as same-section false positive
    """
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
        if len(citations) < 2:
            continue
        if enforce_sections:
            sections = [int(s) for s in g.get("sections", []) if str(s).isdigit()]
            if len(set(sections)) < 2:
                continue  # same-section false positive — discard
        groups.append(
            {"theme": str(g.get("theme", "")), "citations": sorted(citations)}
        )
    return groups


class IdentifyRedundancyHandler(BaseHandler):
    """Identify semantically redundant claims across batched synthesis sections."""

    step_type: str = "consensus_identify_redundancy_v8_0"

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

        extracted_sentences_raw = (
            self._resolve_input(resolver, step, "extracted_sentences", inputs)
            if "extracted_sentences" in inputs
            else None
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

        if extracted_sentences_raw and isinstance(extracted_sentences_raw, list):
            draft = _format_sentences_for_comparison(extracted_sentences_raw)
            using_sections = True
            logger.info(
                "Step '%s': using %d pre-extracted sentences",
                step.id,
                len(extracted_sentences_raw),
            )
        else:
            draft = _compose_draft(answer, batch_texts)
            using_sections = bool(batch_texts)
            logger.info(
                "Step '%s': composing draft as %s",
                step.id,
                f"{len(batch_texts)} labeled sections"
                if using_sections
                else "flat answer",
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
            merge_groups = _parse_merge_groups(raw, enforce_sections=using_sections)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("Step '%s': failed to parse merge groups — %s", step.id, exc)

        latency_ms = (time.monotonic() - start_time) * 1000
        if merge_groups:
            for g in merge_groups:
                logger.info(
                    "Step '%s': merge group '%s' citations=%s",
                    step.id,
                    g.get("theme", ""),
                    g["citations"],
                )
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
                    f"Step '{step.id}': consensus_identify_redundancy_v8_0 requires '{field}' "
                    "in handler_inputs"
                )
        if not step.prompt_ref:
            errors.append(
                f"Step '{step.id}': consensus_identify_redundancy_v8_0 requires 'prompt_ref'"
            )
        return errors
