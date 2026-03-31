"""Reranking with cross-encoder (default/fast) or LLM sliding-window (generative).

Receives post-RRF chunks from the retrieve step, optionally reranks them,
fuses reranker scores with the prior retrieval signal, and formats the final context.

Reranking is a final adjustment, not a replacement for retrieval order.  The prior
(RRF + metadata-boost score) stays dominant so retrieval evidence is not overturned
by a marginally higher reranker score on a weak chunk.

Modes (``rerank_mode`` pipeline option):
    ``cross_encoder`` (default) — Score each (query, chunk) pair via a cross-encoder
        model in a single forward pass.  ~80-175 ms for 14 passages on GPU.  No text
        generation.  Fusion formula::

            final = prior_weight * (prior / max_prior)
                  + (1 - prior_weight) * (ce_score / max_ce)

        Default ``prior_weight`` = 0.70, keeping retrieval signal dominant.
        Movement is then bounded to ±``max_movement`` positions (default 3).

    ``generative`` — LLM sliding-window reranking; JSON output with rank +
        confidence + reason per chunk.  ~4-7 s for 14 chunks.  Same weighted
        fusion and bounded-movement cap applied after score aggregation.

When ``rerank_enabled`` is false, chunks pass straight to formatting.
"""

from __future__ import annotations

import json
import time as _time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.dag import ResponseTruncatedError
from systems.pipeline.core.events.step import RagRerankCompleted
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from .context_formatting import ChunkData, format_context, merge_adjacent_chunks
from .rerank_scoring import (
    aggregate_window_scores,
    apply_bounded_movement,
    build_candidate_block,
    build_windows,
)

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class RagRerankAssembleHandler(BaseHandler):
    """Reranking (cross-encoder or generative) with bounded movement and context formatting.

    Default mode is ``cross_encoder``: a single forward pass that scores each
    (query, chunk) pair without text generation.  ``generative`` mode uses a
    sliding-window LLM for richer but slower reranking.

    Both modes apply the same weighted fusion and bounded-movement cap:
    the retrieval prior remains the dominant signal; the reranker can shift a
    chunk at most ``max_movement`` positions from its pre-rerank rank.

    When reranking is disabled, passes chunks directly to formatting.
    """

    step_type: str = "rag_rerank_assemble_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        resolver = NamespaceResolver(context)
        try:
            chunks_data: list[ChunkData] = self._resolve_input(
                resolver, step, "chunks_data", step.handler_inputs
            )
        except KeyError:
            logger.warning(
                "Step '%s': upstream 'chunks' key absent — no chunks to process",
                step.id,
            )
            chunks_data = []

        # Optional facets from refine_facets — formatted as compact label: terms lines
        # so the reranker can weight chunks that match ALL facet dimensions higher.
        rerank_facets: str = ""
        if step.handler_inputs and "facet_result" in step.handler_inputs:
            try:
                facet_raw = self._resolve_input(
                    resolver, step, "facet_result", step.handler_inputs
                )
                if isinstance(facet_raw, list):
                    lines = []
                    for facet in facet_raw:
                        if not isinstance(facet, dict):
                            continue
                        label = str(facet.get("label", "")).replace("_", " ")
                        terms = [str(t) for t in facet.get("terms", []) if t]
                        if terms:
                            lines.append(f"  {label}: {', '.join(terms)}")
                    rerank_facets = "\n".join(lines)
            except Exception:
                logger.warning(
                    "Step '%s': failed to resolve facet_result for reranking",
                    step.id,
                    exc_info=True,
                )

        effective = context.options
        rerank_enabled = bool(effective.get("rerank_enabled", False))
        rerank_mode = str(effective.get("rerank_mode", "generative"))
        include_section_headings = bool(
            effective.get("rag_include_section_headings", False)
        )
        include_source_titles = bool(effective.get("rag_include_source_titles", False))

        if not chunks_data or not rerank_enabled or len(chunks_data) <= 3:
            context_text = format_context(
                merge_adjacent_chunks(chunks_data) if chunks_data else [],
                include_section_headings=include_section_headings,
                include_source_titles=include_source_titles,
            )
            self._emit_rerank_event(
                context,
                step,
                rerank_enabled=False,
                model_id=None,
                chunks_in=len(chunks_data) if chunks_data else 0,
                chunks_out=len(chunks_data) if chunks_data else 0,
                windows=0,
                max_move=0,
                seconds=0.0,
            )
            return StepOutput(
                raw=context_text,
                json={
                    "chunks_reranked": len(chunks_data) if chunks_data else 0,
                    "rerank_enabled": False,
                },
            )

        if rerank_mode == "cross_encoder":
            return await self._execute_cross_encoder_rerank(
                step,
                context,
                chunks_data,
                max_candidates=int(effective.get("rerank_max_candidates", 14)),
                max_movement=int(effective.get("rerank_max_movement", 3)),
                prior_weight=float(effective.get("rerank_prior_weight", 0.70)),
            )

        return await self._execute_rerank(
            step,
            context,
            chunks_data,
            window_size=int(effective.get("rerank_window_size", 5)),
            overlap=int(effective.get("rerank_overlap", 1)),
            max_candidates=int(effective.get("rerank_max_candidates", 14)),
            max_movement=int(effective.get("rerank_max_movement", 3)),
            prior_weight=float(effective.get("rerank_prior_weight", 0.70)),
            rerank_facets=rerank_facets,
        )

    async def _execute_rerank(
        self,
        step: StepConfig,
        context: PipelineContext,
        chunks_data: list[ChunkData],
        *,
        window_size: int,
        overlap: int,
        max_candidates: int,
        max_movement: int,
        prior_weight: float,
        rerank_facets: str = "",
    ) -> StepOutput:
        """Run sliding-window LLM reranking, fuse scores, format context."""
        _start = _time.monotonic()
        include_section_headings = bool(
            context.options.get("rag_include_section_headings", False)
        )
        include_source_titles = bool(
            context.options.get("rag_include_source_titles", False)
        )

        candidates = chunks_data[:max_candidates]
        chunk_ids = [c["content_hash"][:8] for c in candidates]
        tail = chunks_data[max_candidates:]

        windows = build_windows(len(candidates), window_size, overlap)

        model_id, model_profile = self._resolve_rerank_model(step, context)
        json_schema = None
        if step.generation_parameters.get("response_format"):
            json_schema = step.generation_parameters["response_format"].get("schema")

        window_rankings: list[dict[str, list[dict[str, Any]]]] = []
        for w_idx, window_indices in enumerate(windows):
            window_chunks = [candidates[i] for i in window_indices]
            candidates_text = "\n\n".join(
                build_candidate_block(c, i)
                for i, c in zip(window_indices, window_chunks)
            )

            template_ctx: dict[str, Any] = {
                "text": context.source_text,
                **context.options,
                "rerank_candidates": candidates_text,
                "rerank_facets": rerank_facets,
            }

            rendered = self._render_prompt(step.prompt_ref, template_ctx, context)

            try:
                call_result = await self._call_model(
                    model_id,
                    rendered.user_prompt,
                    step,
                    context,
                    rendered.system_prompt,
                    temperature=step.generation_parameters.get("temperature", 0.2),
                    max_tokens=step.generation_parameters.get("max_tokens", 512),
                    json_schema=json_schema,
                    call_label=f"rerank_w{w_idx}",
                    model_id_is_resolved=True,
                    model_profile=model_profile,
                )
            except ResponseTruncatedError:
                raise
            except Exception as e:
                logger.error(
                    "rerank window %d LLM call failed: %s", w_idx, e, exc_info=True
                )
                window_rankings.append({"ranking": []})
                continue

            try:
                parsed = json.loads(call_result.content)
                window_rankings.append(parsed)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "rerank window %d returned invalid JSON, skipping", w_idx
                )
                window_rankings.append({"ranking": []})

        llm_scores, confidence_map = aggregate_window_scores(
            window_rankings, windows, chunk_ids
        )

        max_prior = max(1.0, max((c["score"] for c in candidates), default=0.0))
        max_llm = max(llm_scores.values(), default=1.0) or 1.0

        final_scores: dict[str, float] = {}
        for c in candidates:
            cid = c["content_hash"][:8]
            prior_norm = c["score"] / max_prior
            llm_norm = llm_scores.get(cid, 0.0) / max_llm
            final_scores[cid] = (
                prior_weight * prior_norm + (1 - prior_weight) * llm_norm
            )

        reranked = apply_bounded_movement(
            candidates, final_scores, max_movement, confidence_map
        )
        all_chunks = reranked + tail

        _seconds = _time.monotonic() - _start

        max_move_observed = 0
        prior_order = {c["content_hash"][:8]: i for i, c in enumerate(candidates)}
        for new_pos, c in enumerate(reranked):
            cid = c["content_hash"][:8]
            old_pos = prior_order.get(cid, new_pos)
            max_move_observed = max(max_move_observed, abs(new_pos - old_pos))

        merged_chunks = merge_adjacent_chunks(all_chunks)
        context_text = format_context(
            merged_chunks,
            include_section_headings=include_section_headings,
            include_source_titles=include_source_titles,
        )

        self._emit_rerank_event(
            context,
            step,
            rerank_enabled=True,
            model_id=model_id,
            chunks_in=len(candidates),
            chunks_out=len(all_chunks),
            windows=len(windows),
            max_move=max_move_observed,
            seconds=_seconds,
        )

        return StepOutput(
            raw=context_text,
            json={
                "chunks_reranked": len(reranked),
                "rerank_enabled": True,
                "windows_evaluated": len(windows),
                "max_rank_movement": max_move_observed,
                "rerank_seconds": round(_seconds, 3),
            },
        )

    async def _execute_cross_encoder_rerank(
        self,
        step: StepConfig,
        context: PipelineContext,
        chunks_data: list[ChunkData],
        *,
        max_candidates: int,
        max_movement: int,
        prior_weight: float,
    ) -> StepOutput:
        """Rerank via cross-encoder — single forward pass, no text generation.

        Score fusion (per candidate)::

            prior_norm = chunk["score"] / max(prior scores)
            ce_norm    = ce_score / max(|ce scores|)
            final      = prior_weight * prior_norm + (1 - prior_weight) * ce_norm

        Default ``prior_weight`` = 0.70, so retrieval order stays dominant.
        After fusion, ``apply_bounded_movement`` caps each chunk's rank shift to
        ±``max_movement`` positions (default 3) relative to its pre-rerank position.
        """
        _start = _time.monotonic()
        include_section_headings = bool(
            context.options.get("rag_include_section_headings", False)
        )
        include_source_titles = bool(
            context.options.get("rag_include_source_titles", False)
        )

        candidates = chunks_data[:max_candidates]
        tail = chunks_data[max_candidates:]
        passages = [c["content"][:2000] for c in candidates]

        ce_ref = str(
            context.options.get("rerank_cross_encoder_model") or "cross_encoder"
        )
        model_id, _profile = self._resolve_rerank_model(
            step, context, model_ref_override=ce_ref
        )

        try:
            rerank_timeout = float(context.options.get("rag_client_timeout", 30.0))
        except (TypeError, ValueError):
            rerank_timeout = 30.0

        proxy_client = context.get_proxy_client()
        result = await proxy_client.rerank(
            model=model_id,
            query=context.source_text,
            passages=passages,
            execution_id=context.execution_id,
            step_id=step.id,
            timeout=rerank_timeout,
        )
        ce_scores_raw: list[float] = result.get("scores", [])
        model_id = result.get("model", model_id)

        if len(ce_scores_raw) != len(candidates):
            logger.warning(
                "Step '%s': cross-encoder returned %d scores for %d candidates, "
                "falling back to pass-through",
                step.id,
                len(ce_scores_raw),
                len(candidates),
            )
            context_text = format_context(
                merge_adjacent_chunks(chunks_data),
                include_section_headings=include_section_headings,
                include_source_titles=include_source_titles,
            )
            self._emit_rerank_event(
                context,
                step,
                rerank_enabled=False,
                model_id=model_id,
                chunks_in=len(candidates),
                chunks_out=len(chunks_data),
                windows=0,
                max_move=0,
                seconds=_time.monotonic() - _start,
            )
            return StepOutput(
                raw=context_text,
                json={
                    "chunks_reranked": len(chunks_data),
                    "rerank_enabled": False,
                    "rerank_mode": "cross_encoder",
                    "rerank_error": "score_count_mismatch",
                },
            )

        max_prior = max(1.0, max((c["score"] for c in candidates), default=0.0))
        max_ce = max(abs(s) for s in ce_scores_raw) or 1.0

        final_scores: dict[str, float] = {}
        for c, ce_score in zip(candidates, ce_scores_raw):
            cid = c["content_hash"][:8]
            prior_norm = c["score"] / max_prior
            ce_norm = ce_score / max_ce
            final_scores[cid] = prior_weight * prior_norm + (1 - prior_weight) * ce_norm

        confidence_map = {c["content_hash"][:8]: "high" for c in candidates}
        reranked = apply_bounded_movement(
            candidates, final_scores, max_movement, confidence_map
        )
        all_chunks = reranked + tail

        _seconds = _time.monotonic() - _start

        max_move_observed = 0
        prior_order = {c["content_hash"][:8]: i for i, c in enumerate(candidates)}
        for new_pos, c in enumerate(reranked):
            cid = c["content_hash"][:8]
            old_pos = prior_order.get(cid, new_pos)
            max_move_observed = max(max_move_observed, abs(new_pos - old_pos))

        merged_chunks = merge_adjacent_chunks(all_chunks)
        context_text = format_context(
            merged_chunks,
            include_section_headings=include_section_headings,
            include_source_titles=include_source_titles,
        )

        self._emit_rerank_event(
            context,
            step,
            rerank_enabled=True,
            model_id=model_id,
            chunks_in=len(candidates),
            chunks_out=len(all_chunks),
            windows=0,
            max_move=max_move_observed,
            seconds=_seconds,
        )

        return StepOutput(
            raw=context_text,
            json={
                "chunks_reranked": len(reranked),
                "rerank_enabled": True,
                "rerank_mode": "cross_encoder",
                "rerank_model": model_id,
                "max_rank_movement": max_move_observed,
                "rerank_seconds": round(_seconds, 3),
            },
        )

    def _emit_rerank_event(
        self,
        context: PipelineContext,
        step: StepConfig,
        *,
        rerank_enabled: bool,
        model_id: str | None,
        chunks_in: int,
        chunks_out: int,
        windows: int,
        max_move: int,
        seconds: float,
    ) -> None:
        """Emit RagRerankCompleted to the event bus for observability."""
        self._publish_bus_event(
            context,
            RagRerankCompleted(
                pipeline_id=context.pipeline.id,
                execution_id=context.execution_id,
                step_name=step.name,
                rerank_enabled=rerank_enabled,
                model_id=model_id,
                chunks_input=chunks_in,
                chunks_output=chunks_out,
                windows_evaluated=windows,
                max_rank_movement_observed=max_move,
                total_rerank_seconds=seconds,
            ),
        )

    def _resolve_rerank_model(
        self,
        step: StepConfig,
        context: PipelineContext,
        *,
        model_ref_override: str | None = None,
    ) -> tuple[str, str | None]:
        """Resolve model ID and profile from models.yaml registry.

        The profile carries ``chat_template_kwargs`` (e.g. ``enable_thinking:
        false``) that must reach the inference engine. Without it, Qwen3 models
        default to thinking mode and spend the entire token budget on hidden
        ``<think>`` blocks, producing empty visible content.

        ``model_ref_override`` selects the cross-encoder alias (see
        ``rerank_cross_encoder_model``) while ``step.model_ref`` remains the
        generative rerank LLM.
        """
        alias = model_ref_override if model_ref_override is not None else step.model_ref
        if alias and alias.startswith("optionsNs."):
            key = alias[len("optionsNs.") :]
            alias = (context.options or {}).get(key, alias)
        registry = context._registry
        try:
            model_config = registry.get_model_config(
                alias,
                domain=context.pipeline.domain,
                search_path=context.pipeline.source_search_path,
            )
            return model_config.model, model_config.profile
        except KeyError:
            resolved = self._resolve_model_alias(alias, context)
            return resolved, None

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        if not step.handler_inputs or "chunks_data" not in step.handler_inputs:
            errors.append(f"Step '{step.id}' missing 'chunks_data' in handler_inputs")
        return errors
