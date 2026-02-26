"""
Embedding-based fact coverage audit. Audit-only, no pipeline side effects.

Measures how well the final answer covers verified facts via embedding
similarity. Logs uncovered facts at WARN and emits coverage_scores /
uncovered_facts in StepOutput.json. Does not trigger re-enrichment or block.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from .shared._dedup_embeddings import get_embeddings
from .shared._text_utils import get_statement_text, strip_fact_citations

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_DEFAULT_COVERAGE_THRESHOLD = 0.70
_DEFAULT_EMBEDDING_MODEL = "embedding"


def _split_sentences(text: str) -> list[str]:
    """Split on sentence boundaries. No external NLP dependency."""
    if not text or not text.strip():
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


class CoverageAuditHandler(BaseHandler):
    """Embedding-based fact coverage audit. Audit-only, no pipeline side effects."""

    step_type: str = "consensus_coverage_audit_v7"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Compute per-fact coverage scores; log uncovered; passthrough raw."""
        resolver = NamespaceResolver(context)
        hi = step.handler_inputs or {}
        final_answer = str(
            self._resolve_input(resolver, step, "final_answer", hi) or ""
        )
        clean_answer = strip_fact_citations(final_answer)

        enabled = step.get_domain_field("enabled")
        if enabled is not None and not enabled:
            return StepOutput(raw=clean_answer, step_id=step.id)

        verified_facts: list[dict[str, Any]] = (
            self._resolve_input(resolver, step, "verified_facts", hi) or []
        )
        if not verified_facts:
            return StepOutput(raw=clean_answer, step_id=step.id)
        sentences = _split_sentences(clean_answer)
        if not sentences:
            scores = [0.0] * len(verified_facts)
            uncovered = [
                {"text": get_statement_text(f), "score": 0.0} for f in verified_facts
            ]
            logger.warning(
                "Step '%s': empty sentences; all %d facts uncovered",
                step.id,
                len(verified_facts),
            )
            return StepOutput(
                raw=clean_answer,
                json={"coverage_scores": scores, "uncovered_facts": uncovered},
                step_id=step.id,
            )
        fact_texts = [get_statement_text(f) for f in verified_facts]
        emb_model = str(
            step.get_domain_field("embedding_model") or _DEFAULT_EMBEDDING_MODEL
        )
        threshold = float(
            step.get_domain_field("coverage_threshold") or _DEFAULT_COVERAGE_THRESHOLD
        )
        all_emb = await get_embeddings(
            sentences + fact_texts, emb_model, context, step_id=step.id
        )
        n_sent = len(sentences)
        sent_emb, fact_emb = all_emb[:n_sent], all_emb[n_sent:]
        coverage_scores: list[float] = []
        uncovered_facts: list[dict[str, Any]] = []
        for i, fact in enumerate(verified_facts):
            raw_best = float((sent_emb @ fact_emb[i]).max()) if sent_emb.size else 0.0
            score = max(0.0, min(1.0, raw_best))
            coverage_scores.append(score)
            if score < threshold:
                uncovered_facts.append(
                    {
                        "text": get_statement_text(fact),
                        "score": round(score, 4),
                        "index": i,
                    }
                )
        covered_count = len(verified_facts) - len(uncovered_facts)
        mean_score = (
            sum(coverage_scores) / len(coverage_scores) if coverage_scores else 0.0
        )
        coverage_pct = covered_count / len(verified_facts) * 100 if verified_facts else 0.0

        if uncovered_facts:
            logger.warning(
                "Step '%s': %d/%d facts below coverage threshold %.2f (%.1f%% covered)",
                step.id,
                len(uncovered_facts),
                len(verified_facts),
                threshold,
                coverage_pct,
            )

        recorder = context.recorder
        if recorder:
            from systems.pipeline.core.events.verification import (
                CoverageAuditCompleted as RecorderCoverageAuditCompleted,
            )

            recorder.emit(
                RecorderCoverageAuditCompleted(
                    step_name=step.id,
                    total_facts=len(verified_facts),
                    covered_count=covered_count,
                    uncovered_count=len(uncovered_facts),
                    mean_score=round(mean_score, 4),
                    coverage_pct=round(coverage_pct, 1),
                    threshold=threshold,
                )
            )

        pipeline_id = str(getattr(context, "pipeline_id", "") or "")
        execution_id = str(getattr(context, "execution_id", "") or "")
        from systems.pipeline.core.events.step import (
            CoverageAuditCompleted as BusCoverageAuditCompleted,
        )

        self._publish_bus_event(
            context,
            BusCoverageAuditCompleted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=step.id,
                total_facts=len(verified_facts),
                covered_count=covered_count,
                uncovered_count=len(uncovered_facts),
                mean_score=round(mean_score, 4),
                coverage_pct=round(coverage_pct, 1),
                threshold=threshold,
            ),
        )

        return StepOutput(
            raw=clean_answer,
            json={
                "coverage_scores": coverage_scores,
                "uncovered_facts": uncovered_facts,
            },
            step_id=step.id,
        )
