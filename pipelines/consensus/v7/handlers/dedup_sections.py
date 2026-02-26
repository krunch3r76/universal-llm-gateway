"""Pair-based within-section deduplication handler.

Splits the synthesized answer into sections, extracts sentences with their
citations, detects duplicate pairs via citation overlap, and optionally
confirms borderline cases with a binary model call.

Designed for small models (7B-14B) that struggle with open-ended redundancy
detection but easily answer "same claim? yes/no" per pair.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_CITE_RE = re.compile(r"\[(?:Fact\s+)?(\d+(?:\s*,\s*(?:Fact\s+)?\d+)*)\]")
_HEADING_RE = re.compile(r"^###\s+(.+)$", re.MULTILINE)


def _extract_citations(text: str) -> set[int]:
    """Extract all [Fact N] or [N] citation indices from a string."""
    indices: set[int] = set()
    for m in _CITE_RE.finditer(text):
        for part in m.group(1).split(","):
            cleaned = re.sub(r"Fact\s*", "", part).strip()
            if cleaned.isdigit():
                indices.add(int(cleaned))
    return indices


def _split_into_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown answer into (heading, body) pairs."""
    parts = _HEADING_RE.split(text)
    sections: list[tuple[str, str]] = []
    i = 1
    while i < len(parts) - 1:
        heading = parts[i].strip()
        body = parts[i + 1].strip()
        sections.append((heading, body))
        i += 2
    return sections


def _split_sentences(body: str) -> list[str]:
    """Split section body into individual sentences/bullets."""
    lines = body.strip().splitlines()
    sentences: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            sentences.append(stripped)
        else:
            for sent in re.split(r"(?<=\])\.\s+", stripped):
                s = sent.strip()
                if s:
                    sentences.append(s)
    return sentences


def _jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


class DedupSectionsHandler(BaseHandler):
    """Remove within-section citation-overlap duplicates from a synthesized answer."""

    step_type: str = "consensus_dedup_sections_v7"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        start_time = time.time()
        hi = step.handler_inputs or {}
        from systems.pipeline.core.execution.resolver import NamespaceResolver

        resolver = NamespaceResolver(context)
        resolved: dict[str, Any] = {
            name: self._resolve_input(resolver, step, name, hi) for name in hi
        }

        artifact = str(resolved.get("artifact", ""))
        overlap_threshold: float = float(
            step.get_domain_field("overlap_threshold") or 0.5
        )
        confirm_with_model: bool = bool(step.get_domain_field("confirm_with_model"))

        prompt_ref = step.get_domain_field("prompt_ref") or step.prompt_ref
        model_id = (
            self._resolve_model_alias(step.model_ref, context)
            if step.model_ref
            else None
        )
        gen_params = step.generation_parameters or {}
        temperature = gen_params.get("temperature", 0.1)

        sections = _split_into_sections(artifact)
        removals: list[dict[str, str]] = []
        total_pt = 0
        total_ct = 0
        model_calls = 0

        for heading, body in sections:
            sentences = _split_sentences(body)
            if len(sentences) < 2:
                continue

            cited: list[tuple[str, set[int]]] = [
                (s, _extract_citations(s)) for s in sentences
            ]

            to_remove: set[int] = set()
            for i in range(len(cited)):
                if i in to_remove:
                    continue
                for j in range(i + 1, len(cited)):
                    if j in to_remove:
                        continue
                    s_i, c_i = cited[i]
                    s_j, c_j = cited[j]
                    overlap = _jaccard(c_i, c_j)

                    if overlap >= overlap_threshold:
                        is_dup = True

                        if confirm_with_model and model_id and prompt_ref:
                            is_dup = await self._confirm_pair(
                                s_i,
                                s_j,
                                prompt_ref,
                                model_id,
                                step,
                                context,
                                temperature,
                            )
                            model_calls += 1

                        if is_dup:
                            keep_idx, drop_idx = (
                                (i, j) if len(c_i) >= len(c_j) else (j, i)
                            )
                            to_remove.add(drop_idx)
                            kept_s = cited[keep_idx][0]
                            dropped_s = cited[drop_idx][0]

                            drop_cites = cited[drop_idx][1]
                            keep_cites = cited[keep_idx][1]
                            orphan_cites = drop_cites - keep_cites
                            if orphan_cites:
                                cite_str = ", ".join(
                                    str(c) for c in sorted(orphan_cites)
                                )
                                kept_s_new = re.sub(
                                    r"\]\.?\s*$",
                                    f", {cite_str}]",
                                    kept_s,
                                )
                                body = body.replace(kept_s, kept_s_new)
                                cited[keep_idx] = (
                                    kept_s_new,
                                    keep_cites | orphan_cites,
                                )

                            removals.append(
                                {
                                    "section": heading,
                                    "kept": cited[keep_idx][0][:80],
                                    "deleted": dropped_s[:80],
                                    "overlap": f"{overlap:.2f}",
                                }
                            )

            if to_remove:
                keep_sentences = [
                    s for idx, (s, _) in enumerate(cited) if idx not in to_remove
                ]
                new_body = "\n".join(keep_sentences)
                artifact = artifact.replace(body, new_body)

        latency_ms = (time.time() - start_time) * 1000
        logger.info(
            "DedupSections: removed %d duplicate(s) across %d section(s) "
            "(threshold=%.2f, model_calls=%d)",
            len(removals),
            len(sections),
            overlap_threshold,
            model_calls,
        )

        return StepOutput(
            raw=artifact,
            json={"removals": removals, "removal_count": len(removals)},
            step_id=step.id,
            latency_ms=latency_ms,
            prompt_tokens=total_pt,
            completion_tokens=total_ct,
            model_call_count=model_calls,
        )

    async def _confirm_pair(
        self,
        sentence_a: str,
        sentence_b: str,
        prompt_ref: str,
        model_id: str,
        step: StepConfig,
        context: PipelineContext,
        temperature: float,
    ) -> bool:
        """Ask the model if two sentences assert the same claim."""
        rendered = self._render_prompt(
            prompt_ref,
            {"sentence_a": sentence_a, "sentence_b": sentence_b},
            context,
            safe=True,
        )
        result = await self._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            system_prompt=rendered.system_prompt,
            temperature=temperature,
            max_tokens=10,
            call_label="confirm_pair",
        )
        return "yes" in result.content.strip().lower()
