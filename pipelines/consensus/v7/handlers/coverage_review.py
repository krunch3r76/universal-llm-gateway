"""Hybrid coverage review: embeddings narrow → LLM adjudicates → LLM inserts.

Phase 1 (embeddings): compute similarity between each verified fact and
the answer sentences. Facts below threshold become candidates.

Phase 2 (LLM adjudicate): given only the candidates + answer, determine
which represent genuinely missing topics vs semantic near-duplicates of
already-covered content.

Phase 3 (LLM insert): for genuinely missing topics, insert into the
appropriate answer sections with citations.

Combines embeddings (fast pre-filter) with LLM judgment (semantic grouping).
Each "brain region" does what it's best at.
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

from .shared._dedup_embeddings import get_embeddings
from .shared._text_utils import get_statement_text, strip_fact_citations

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_DEFAULT_SIMILARITY_THRESHOLD = 0.70
_DEFAULT_EMBEDDING_MODEL = "embedding"

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    if not text or not text.strip():
        return []
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


class CoverageReviewHandler(BaseHandler):
    """Hybrid embeddings + LLM coverage review."""

    step_type: str = "consensus_coverage_review_v7"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        start_time = time.time()
        resolver = NamespaceResolver(context)
        hi = step.handler_inputs or {}
        artifact = str(self._resolve_input(resolver, step, "artifact", hi) or "")
        verified_facts: list[dict[str, Any]] = (
            self._resolve_input(resolver, step, "verified_facts", hi) or []
        )
        question = str(self._resolve_input(resolver, step, "question", hi) or "")

        if not verified_facts or not artifact.strip():
            return StepOutput(raw=artifact, step_id=step.id)

        gen_params = step.generation_parameters or {}
        temperature = gen_params.get("temperature", 0.1)
        max_tokens = gen_params.get("max_tokens")
        total_pt = 0
        total_ct = 0
        call_count = 0

        # ── Phase 1: embedding pre-filter ──────────────────────────────
        candidates = await self._embedding_phase(
            step, context, artifact, verified_facts
        )

        if not candidates:
            logger.info("Step '%s': all facts covered by embeddings", step.id)
            return StepOutput(
                raw=artifact,
                json={
                    "candidates": 0,
                    "genuinely_missing": 0,
                    "phase": "embedding_pass",
                },
                step_id=step.id,
                latency_ms=(time.time() - start_time) * 1000,
            )

        logger.info(
            "Step '%s': %d/%d facts below threshold → LLM adjudication",
            step.id,
            len(candidates),
            len(verified_facts),
        )

        # ── Phase 2: LLM adjudication ─────────────────────────────────
        adjudicate_ref = str(step.get_domain_field("adjudicate_prompt_ref") or "")
        adjudicate_model = self._resolve_model_alias(
            step.get_domain_field("adjudicate_model_ref") or step.model_ref,
            context,
        )

        candidate_text = "\n".join(
            f"[{c['index'] + 1}] {c['text']} (similarity: {c['score']:.2f})"
            for c in candidates
        )
        rendered = self._render_prompt(
            adjudicate_ref,
            {
                "artifact": artifact,
                "candidate_facts": candidate_text,
                "question": question,
            },
            context,
            safe=True,
        )

        adj_result = await self._call_model(
            adjudicate_model,
            rendered.user_prompt,
            step,
            context,
            system_prompt=rendered.system_prompt,
            temperature=temperature,
            max_tokens=1024,
            json_schema={
                "type": "object",
                "properties": {
                    "missing_indices": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["missing_indices"],
            },
            call_label="adjudicate",
        )
        total_pt += adj_result.prompt_tokens
        total_ct += adj_result.completion_tokens
        call_count += 1

        try:
            missing_indices: list[int] = json.loads(adj_result.content).get(
                "missing_indices",
                [],
            )
        except json.JSONDecodeError:
            logger.warning("Step '%s': adjudication JSON parse failure", step.id)
            missing_indices = []

        # Validate: only allow indices that were actually candidates
        candidate_indices_1based = {c["index"] + 1 for c in candidates}
        missing_indices = [i for i in missing_indices if i in candidate_indices_1based]

        if not missing_indices:
            logger.info("Step '%s': LLM confirms all candidates are covered", step.id)
            return StepOutput(
                raw=artifact,
                json={
                    "candidates": len(candidates),
                    "genuinely_missing": 0,
                    "phase": "adjudicate_pass",
                },
                step_id=step.id,
                latency_ms=(time.time() - start_time) * 1000,
                prompt_tokens=total_pt,
                completion_tokens=total_ct,
                model_call_count=call_count,
            )

        logger.info(
            "Step '%s': %d genuinely missing topics → inserting",
            step.id,
            len(missing_indices),
        )

        # ── Phase 3: LLM insertion ────────────────────────────────────
        missing_text = "\n".join(
            f"[Fact {idx}] {get_statement_text(verified_facts[idx - 1])}"
            for idx in missing_indices
            if 1 <= idx <= len(verified_facts)
        )
        insert_ref = str(step.get_domain_field("insert_prompt_ref") or "")
        insert_model = self._resolve_model_alias(
            step.get_domain_field("insert_model_ref") or step.model_ref,
            context,
        )

        insert_rendered = self._render_prompt(
            insert_ref,
            {"artifact": artifact, "uncited_facts": missing_text, "question": question},
            context,
            safe=True,
        )

        insert_result = await self._call_model(
            insert_model,
            insert_rendered.user_prompt,
            step,
            context,
            system_prompt=insert_rendered.system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            call_label="insert_missing",
        )
        total_pt += insert_result.prompt_tokens
        total_ct += insert_result.completion_tokens
        call_count += 1

        return StepOutput(
            raw=insert_result.content.strip(),
            json={
                "candidates": len(candidates),
                "genuinely_missing": len(missing_indices),
                "missing_indices": missing_indices,
                "phase": "insert_completed",
            },
            step_id=step.id,
            latency_ms=(time.time() - start_time) * 1000,
            prompt_tokens=total_pt,
            completion_tokens=total_ct,
            model_call_count=call_count,
        )

    async def _embedding_phase(
        self,
        step: StepConfig,
        context: PipelineContext,
        artifact: str,
        verified_facts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Identify candidate uncovered facts via embedding similarity."""
        clean_answer = strip_fact_citations(artifact)
        sentences = _split_sentences(clean_answer)
        if not sentences:
            return [
                {"text": get_statement_text(f), "score": 0.0, "index": i}
                for i, f in enumerate(verified_facts)
            ]

        fact_texts = [get_statement_text(f) for f in verified_facts]
        emb_model = str(
            step.get_domain_field("embedding_model") or _DEFAULT_EMBEDDING_MODEL,
        )
        threshold = float(
            step.get_domain_field("similarity_threshold")
            or _DEFAULT_SIMILARITY_THRESHOLD,
        )

        all_emb = await get_embeddings(
            sentences + fact_texts,
            emb_model,
            context,
            step_id=step.id,
        )
        n_sent = len(sentences)
        sent_emb, fact_emb = all_emb[:n_sent], all_emb[n_sent:]

        candidates: list[dict[str, Any]] = []
        for i in range(len(verified_facts)):
            raw_best = float((sent_emb @ fact_emb[i]).max()) if sent_emb.size else 0.0
            score = max(0.0, min(1.0, raw_best))
            if score < threshold:
                candidates.append(
                    {
                        "text": get_statement_text(verified_facts[i]),
                        "score": round(score, 4),
                        "index": i,
                    }
                )
        return candidates
