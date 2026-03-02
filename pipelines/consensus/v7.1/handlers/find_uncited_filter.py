"""
Deterministic citation-presence filter for synthesize QA.

Replaces the LLM-based find_uncited_filter step. For every sentence the
upstream scan marked UNCITED, applies a regex check to the sentence text:
if the sentence contains a bracket numeric citation ([N], [N, M], [N-M],
etc.) the upstream verdict is overridden and the sentence is treated as CITED.

This eliminates the class of 7B false positives where the scan incorrectly
computes last2/last1 for sentences ending with multi-digit or multi-number
citations, causing cited sentences to be mislabelled UNCITED.

Invariants:
    ∀ s ∈ sentence_checks: verdict(s) = "UNCITED" ∧ has_bracket_citation(s)
        ⟹ s ∉ output.uncited_sentences
    ∀ s ∈ sentence_checks: verdict(s) = "UNCITED" ∧ ¬has_bracket_citation(s)
        ⟹ s ∈ output.uncited_sentences
    ∀ s ∈ sentence_checks: verdict(s) = "CITED"
        ⟹ s ∉ output.uncited_sentences  (passthrough from scan)
"""

from __future__ import annotations

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

# Matches any bracket group containing at least one digit.
# Covers: [1], [4], [10, 12, 15], [7-9], [1–5], [see 4], [cf. 19], etc.
_BRACKET_CITATION_RE = re.compile(r"\[[^\]]*\d[^\]]*\]")


def _has_bracket_citation(text: str) -> bool:
    """Return True if text contains a bracketed numeric citation."""
    return bool(_BRACKET_CITATION_RE.search(text))


class FindUncitedFilterHandler(BaseHandler):
    """
    Deterministic filter: override false UNCITED verdicts via regex citation check.

    Input:  sentence_checks — list of {sentence, verdict, last12, last2, last1}
    Output: uncited_sentences — list of sentence strings that genuinely lack citations
    """

    step_type: str = "consensus_find_uncited_filter_v7_1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        start_time = time.time()
        resolver = NamespaceResolver(context)

        sentence_checks: list[dict[str, Any]] = (
            self._resolve_input(resolver, step, "sentence_checks", step.handler_inputs)
            or []
        )

        uncited_sentences: list[str] = []
        overridden: list[str] = []

        for entry in sentence_checks:
            verdict = str(entry.get("verdict", ""))
            sentence = str(entry.get("sentence", ""))
            if verdict != "UNCITED":
                continue
            if _has_bracket_citation(sentence):
                # Upstream scan error: sentence has a citation → suppress FP
                preview = sentence[:80] + "…" if len(sentence) > 80 else sentence
                overridden.append(preview)
            else:
                uncited_sentences.append(sentence)

        latency_ms = (time.time() - start_time) * 1000

        if overridden:
            logger.info(
                "Step '%s': suppressed %d false UNCITED verdict(s) via bracket-citation check (%.1fms): %s",
                step.id,
                len(overridden),
                latency_ms,
                overridden,
            )

        logger.info(
            "Step '%s': %d genuinely uncited sentence(s) remaining (%.1fms)",
            step.id,
            len(uncited_sentences),
            latency_ms,
        )

        return StepOutput(
            raw="",
            json={"uncited_sentences": uncited_sentences},
            step_id=step.id,
            latency_ms=latency_ms,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        if "sentence_checks" not in (step.handler_inputs or {}):
            errors.append(
                f"Step '{step.id}' missing 'sentence_checks' in handler_inputs"
            )
        return errors
