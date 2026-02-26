"""Two-phase outline review: programmatic coverage loop + LLM quality loop.

Phase 1 (coverage): programmatic set-difference identifies missing fact
indices, an LLM reviser receives the exact missing indices + their texts
and inserts them. Loops until coverage >= threshold or budget exhausted.

Phase 2 (quality): an LLM critic evaluates grouping, ordering, and section
headings. A reviser fixes identified issues. Loops until the critic accepts
or budget exhausted.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any, cast, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from .shared._chain_utils import strip_json_fences

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


def _extract_fact_texts(value: Any) -> list[str]:
    """Extract fact text strings from verified-facts input shapes."""
    if isinstance(value, list) and value:
        if isinstance(value[0], dict):
            return [
                str(item.get("text", "")).strip() for item in value if item.get("text")
            ]
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [line.strip() for line in value.splitlines() if line.strip()]
    return []


def _parse_outline(raw: str) -> dict[str, Any] | None:
    """Parse outline JSON, stripping markdown fences. Returns None on failure."""
    try:
        parsed = json.loads(strip_json_fences(raw))
        if isinstance(parsed, dict) and isinstance(parsed.get("sections"), list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _collect_assigned(sections: list[dict[str, Any]]) -> set[int]:
    """Collect all assigned fact indices from outline sections."""
    assigned: set[int] = set()
    for section in sections:
        indices = section.get("fact_indices")
        if isinstance(indices, list):
            assigned.update(i for i in indices if isinstance(i, int) and i > 0)
    return assigned


def _deduplicate_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate fact indices across sections, keeping first occurrence.

    Models sometimes assign the same fact to multiple sections despite
    instructions.  Duplicates cause the synthesizer to produce repeated
    content, which cascades into over-deletion by redundancy reviewers.
    """
    seen: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for section in sections:
        raw = section.get("fact_indices", [])
        unique = [i for i in raw if isinstance(i, int) and i > 0 and i not in seen]
        seen.update(unique)
        deduped.append({**section, "fact_indices": unique})
    return deduped


def _filter_phantom_overlaps(
    decision: dict[str, Any],
    outline: dict[str, Any],
) -> dict[str, Any]:
    """Remove overlap claims for indices not actually duplicated across sections.

    Models hallucinate overlap (e.g. confusing adjacent indices 30/31).
    If all claimed overlaps are phantom → override action to 'accept'.
    """
    if decision.get("action") != "revise":
        return decision

    index_section_count: dict[int, int] = {}
    for section in outline.get("sections", []):
        for idx in section.get("fact_indices", []):
            if isinstance(idx, int) and idx > 0:
                index_section_count[idx] = index_section_count.get(idx, 0) + 1
    duplicated: set[int] = {idx for idx, n in index_section_count.items() if n > 1}

    filtered: list[str] = []
    phantom_count = 0
    for issue in decision.get("issues", []):
        if issue.lower().startswith("overlap:"):
            m = re.search(r"\b(\d+)\b", issue)
            if m and int(m.group(1)) not in duplicated:
                phantom_count += 1
                logger.info(
                    "Phantom overlap claim filtered: index %d not duplicated. Issue: %s",
                    int(m.group(1)),
                    issue,
                )
                continue
        filtered.append(issue)

    if phantom_count == 0:
        return decision

    if not filtered:
        logger.info(
            "All %d overlap claims were phantom — overriding assessor to 'accept'",
            phantom_count,
        )
        return {**decision, "action": "accept", "issues": []}

    return {**decision, "issues": filtered}


def _format_numbered_facts(facts: list[str]) -> str:
    return "\n".join(f"[{i}] {fact}" for i, fact in enumerate(facts, 1))


def _format_missing_facts(missing: list[int], facts: list[str]) -> str:
    """Format missing fact indices with their text for the reviser."""
    lines = []
    for idx in sorted(missing):
        if 1 <= idx <= len(facts):
            lines.append(f"[{idx}] {facts[idx - 1]}")
    return "\n".join(lines)


def _collect_oversized(
    sections: list[dict[str, Any]],
    threshold: int,
) -> list[str]:
    """Return headings of sections exceeding the per-section index limit.

    ∀ s ∈ sections: len(s.fact_indices) > threshold ⟹ s.heading ∈ result.
    Sections with missing or non-string heading are labelled "(unnamed section)"
    so callers can still report them without crashing.
    """
    result = []
    for s in sections:
        if not isinstance(s.get("fact_indices"), list):
            continue
        if len(s["fact_indices"]) > threshold:
            heading = s.get("heading")
            result.append(heading if isinstance(heading, str) and heading else "(unnamed section)")
    return result


_STOPWORDS = frozenset("a an the is in with of to and or not are be has have for its it this that at by as was were also may been due".split())


def _restore_missing_indices(
    outline: dict[str, Any],
    total_facts: int,
    fact_texts: list[str] | None = None,
) -> dict[str, Any]:
    """Re-insert dropped indices, placing each near its closest keyword neighbour."""
    sections = outline.get("sections", [])
    if not sections:
        return outline
    assigned = _collect_assigned(sections)
    missing = sorted(set(range(1, total_facts + 1)) - assigned)
    if not missing:
        return outline
    last = sections[-1]
    indices: list[int] = list(last.get("fact_indices") or [])
    for m_idx in missing:
        best_pos = len(indices)
        if fact_texts and 1 <= m_idx <= len(fact_texts):
            m_words = set(fact_texts[m_idx - 1].lower().split()) - _STOPWORDS
            best_score = 0
            for pos, ei in enumerate(indices):
                if 1 <= ei <= len(fact_texts):
                    score = len(m_words & (set(fact_texts[ei - 1].lower().split()) - _STOPWORDS))
                    if score > best_score:
                        best_score, best_pos = score, pos + 1
        indices.insert(best_pos, m_idx)
    last["fact_indices"] = indices
    logger.info("Quality reviser dropped %d indices — restored (smart-positioned): %s", len(missing), missing)
    return {**outline, "sections": sections}


class OutlineReviewHandler(BaseHandler):
    """Two-phase outline review: programmatic coverage + LLM quality critique."""

    step_type: str = "consensus_outline_review_v7"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        start_time = time.time()
        resolver = NamespaceResolver(cast(Any, context))
        hi = step.handler_inputs or {}
        resolved: dict[str, Any] = {
            name: self._resolve_input(resolver, step, name, hi) for name in hi
        }

        fact_texts = _extract_fact_texts(resolved.get("verified_facts"))
        total_facts = len(fact_texts)
        question = str(resolved.get("question", ""))
        outline_raw = str(resolved.get("artifact", ""))

        cfg = _ReviewConfig.from_step(step)
        gen_params = step.generation_parameters or {}
        temperature = gen_params.get("temperature", 0.1)
        max_tokens = gen_params.get("max_tokens")
        model_id = self._resolve_model_alias(step.model_ref, context)

        total_pt = 0
        total_ct = 0
        call_count = 0

        # ── Phase 1: coverage + oversized loop ─────────────────────────
        coverage_iters_used = 0
        coverage_iter_log: list[dict[str, Any]] = []
        for i in range(cfg.max_coverage_iterations):
            parsed = _parse_outline(outline_raw)
            if not parsed or total_facts == 0:
                break

            assigned = _collect_assigned(parsed["sections"])
            all_indices = set(range(1, total_facts + 1))
            missing = sorted(all_indices - assigned)
            coverage = len(assigned & all_indices) / total_facts
            oversized = _collect_oversized(parsed["sections"], cfg.oversized_threshold)

            logger.info(
                "Outline review '%s' coverage iter %d: %d/%d (%.0f%%), %d missing, %d oversized",
                step.id,
                i,
                len(assigned & all_indices),
                total_facts,
                coverage * 100,
                len(missing),
                len(oversized),
            )

            if coverage >= cfg.coverage_threshold and not oversized:
                coverage_iter_log.append({
                    "iteration": i,
                    "missing_count": 0,
                    "oversized_count": 0,
                    "exit": "clean",
                })
                break

            coverage_iter_log.append({
                "iteration": i,
                "missing_count": len(missing),
                "oversized_count": len(oversized),
                "oversized_headings": oversized,
                "exit": "revise",
            })

            coverage_iters_used = i + 1
            revise_model = model_id
            if cfg.coverage_pool:
                revise_model = self._resolve_model_alias(
                    cfg.coverage_pool[i % len(cfg.coverage_pool)],
                    context,
                )

            rendered = self._render_prompt(
                cfg.coverage_prompt_ref,
                {
                    "artifact": outline_raw,
                    "missing_indices": ", ".join(str(m) for m in missing),
                    "missing_facts": _format_missing_facts(missing, fact_texts),
                    "oversized_sections": ", ".join(oversized) if oversized else "(none)",
                    "oversized_threshold": str(cfg.oversized_threshold),
                    "verified_facts": _format_numbered_facts(fact_texts),
                    "question": question,
                    "total_facts": str(total_facts),
                    "assigned_count": str(len(assigned & all_indices)),
                    "missing_count": str(len(missing)),
                },
                context,
                safe=True,
            )

            result = await self._call_model(
                revise_model,
                rendered.user_prompt,
                step,
                context,
                system_prompt=rendered.system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                call_label=f"coverage_revise_{i}",
            )
            total_pt += result.prompt_tokens
            total_ct += result.completion_tokens
            call_count += 1
            outline_raw = result.content.strip()

        # ── Deduplicate indices across sections ────────────────────────
        dedup_parsed = _parse_outline(outline_raw)
        if dedup_parsed:
            dedup_parsed["sections"] = _deduplicate_sections(dedup_parsed["sections"])
            outline_raw = json.dumps(dedup_parsed, indent=2)

        # ── Phase 2: quality loop ───────────────────────────────────────
        quality_iters_used = 0
        for i in range(cfg.max_quality_iterations):
            numbered_facts = _format_numbered_facts(fact_texts)
            critique_model = model_id
            if cfg.quality_pool:
                critique_model = self._resolve_model_alias(
                    cfg.quality_pool[i % len(cfg.quality_pool)],
                    context,
                )

            rendered = self._render_prompt(
                cfg.quality_critique_ref,
                {
                    "artifact": outline_raw,
                    "verified_facts": numbered_facts,
                    "question": question,
                },
                context,
                safe=True,
            )

            critique_result = await self._call_model(
                critique_model,
                rendered.user_prompt,
                step,
                context,
                system_prompt=rendered.system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                json_schema=cfg.assess_schema,
                call_label=f"quality_assess_{i}",
            )
            total_pt += critique_result.prompt_tokens
            total_ct += critique_result.completion_tokens
            call_count += 1

            try:
                decision: dict[str, Any] = json.loads(critique_result.content)
            except json.JSONDecodeError:
                logger.warning(
                    "Outline review '%s' quality iter %d: JSON parse failure",
                    step.id,
                    i,
                )
                break

            # Remove overlap claims the model hallucinated (index not actually duplicated).
            assess_outline = _parse_outline(outline_raw)
            if assess_outline:
                decision = _filter_phantom_overlaps(decision, assess_outline)

            # After the first revision, accept if no structural overlap issues remain.
            # Grouping/ordering are aesthetic; further iterations oscillate without converging.
            if decision.get("action") == "accept":
                quality_iters_used = i + 1
                break

            if i > 0:
                has_overlap = any(
                    iss.lower().startswith("overlap:") for iss in decision.get("issues", [])
                )
                if not has_overlap:
                    logger.info(
                        "Outline quality iter %d: no structural overlap after %d revision(s) — accepting (soft issues deferred)",
                        i,
                        i,
                    )
                    quality_iters_used = i + 1
                    break

            quality_iters_used = i + 1
            issues = decision.get("issues", [])
            assess_issues = (
                "\n".join(f"- {issue}" for issue in issues)
                if issues
                else "(none)"
            )
            revise_rendered = self._render_prompt(
                cfg.quality_revise_ref,
                {
                    "artifact": outline_raw,
                    "assess_reason": decision.get("reason", ""),
                    "assess_issues": assess_issues,
                    "assess_target": decision.get("target", ""),
                    "verified_facts": _format_numbered_facts(fact_texts),
                    "question": question,
                },
                context,
                safe=True,
            )

            revise_result = await self._call_model(
                model_id,
                revise_rendered.user_prompt,
                step,
                context,
                system_prompt=revise_rendered.system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                call_label=f"quality_revise_{i}",
            )
            total_pt += revise_result.prompt_tokens
            total_ct += revise_result.completion_tokens
            call_count += 1
            outline_raw = revise_result.content.strip()

            # Deduplicate then restore after each quality revision.
            # Deduplication first: the reviser may introduce new duplicates while
            # restructuring; duplicates corrupt synthesis and re-trigger overlap
            # claims in the next assess cycle.
            revised_parsed = _parse_outline(outline_raw)
            if revised_parsed and total_facts > 0:
                revised_parsed["sections"] = _deduplicate_sections(revised_parsed["sections"])
                revised_parsed = _restore_missing_indices(revised_parsed, total_facts, fact_texts)
                outline_raw = json.dumps(revised_parsed)

        # ── Final metrics ───────────────────────────────────────────────
        final_assigned = 0
        final_missing: list[int] = []
        final_oversized: list[str] = []
        final_parsed = _parse_outline(outline_raw)
        if final_parsed and total_facts > 0:
            assigned = _collect_assigned(final_parsed["sections"])
            valid = assigned & set(range(1, total_facts + 1))
            final_assigned = len(valid)
            final_missing = sorted(set(range(1, total_facts + 1)) - valid)
            final_oversized = _collect_oversized(final_parsed["sections"], cfg.oversized_threshold)

        latency_ms = (time.time() - start_time) * 1000
        return StepOutput(
            raw=outline_raw,
            json={
                "total_facts": total_facts,
                "facts_assigned": final_assigned,
                "missing_indices": final_missing,
                "oversized_sections": final_oversized,
                "coverage_pct": round(final_assigned / total_facts * 100, 1)
                if total_facts
                else 0,
                "coverage_iterations": coverage_iters_used,
                "coverage_iter_log": coverage_iter_log,
                "quality_iterations": quality_iters_used,
            },
            step_id=step.id,
            latency_ms=latency_ms,
            prompt_tokens=total_pt,
            completion_tokens=total_ct,
            model_call_count=call_count,
        )


class _ReviewConfig:
    """Domain fields for outline review step."""

    __slots__ = (
        "coverage_threshold",
        "oversized_threshold",
        "max_coverage_iterations",
        "max_quality_iterations",
        "coverage_prompt_ref",
        "quality_critique_ref",
        "quality_revise_ref",
        "coverage_pool",
        "quality_pool",
        "assess_schema",
    )

    def __init__(
        self,
        *,
        coverage_threshold: float,
        oversized_threshold: int,
        max_coverage_iterations: int,
        max_quality_iterations: int,
        coverage_prompt_ref: str,
        quality_critique_ref: str,
        quality_revise_ref: str,
        coverage_pool: list[str],
        quality_pool: list[str],
        assess_schema: dict[str, Any] | None,
    ) -> None:
        self.coverage_threshold = coverage_threshold
        self.oversized_threshold = oversized_threshold
        self.max_coverage_iterations = max_coverage_iterations
        self.max_quality_iterations = max_quality_iterations
        self.coverage_prompt_ref = coverage_prompt_ref
        self.quality_critique_ref = quality_critique_ref
        self.quality_revise_ref = quality_revise_ref
        self.coverage_pool = coverage_pool
        self.quality_pool = quality_pool
        self.assess_schema = assess_schema

    @classmethod
    def from_step(cls, step: StepConfig) -> _ReviewConfig:
        gdf = step.get_domain_field
        return cls(
            coverage_threshold=gdf("coverage_threshold") or 0.90,
            oversized_threshold=int(gdf("oversized_threshold") or 15),
            max_coverage_iterations=gdf("max_coverage_iterations") or 6,
            max_quality_iterations=gdf("max_quality_iterations") or 3,
            coverage_prompt_ref=gdf("coverage_prompt_ref") or "",
            quality_critique_ref=gdf("quality_critique_ref") or "",
            quality_revise_ref=gdf("quality_revise_ref") or "",
            coverage_pool=gdf("coverage_pool") or [],
            quality_pool=gdf("quality_pool") or [],
            assess_schema=gdf("assess_schema"),
        )
