"""
Multi-query RAG retrieval with reciprocal rank fusion (RRF).

Reads structured output from an upstream query-rewriting step, executes
parallel RAG searches, and merges results via RRF into a single ranked
context block.

Tunable resolution (per-request):
    runtime pipeline_options  >  profile[consumer_model]  >  scope_defaults[scope]  >  YAML defaults

Profiles loaded from ``pipelines/rag/retrieval-profiles.yaml`` (cached after first load).

Invariants:
- ∀ execute(): returns StepOutput.raw = formatted context text (never empty string)
- ∀ needs_retrieval=false: returns sentinel (generation step handles gracefully)
- ∀ RRF merge: deduplicates by chunk content hash, scores by rank only
"""

from __future__ import annotations

import asyncio
import time as _time
from typing import TYPE_CHECKING, Any, override
from urllib.parse import urlparse

from systems.pipeline.core.constants import (
    RAG_NO_RESULTS_SENTINEL as _NO_RESULTS_SENTINEL,
)
from systems.pipeline.core.constants import (
    RAG_NO_RETRIEVAL_SENTINEL as _NO_RETRIEVAL_SENTINEL,
)
from systems.pipeline.core.events.step import (
    RagMetadataBoostApplied,
    RagNeighborExpansionCompleted,
    RagRetrievalBibliographyFiltered,
    RagRetrievalCompleted,
    RagRetrievalFailed,
    RagRetrievalParamsResolved,
    RagRetrievalRetryNotImproved,
    RagRetrievalRetrySucceeded,
    RagRetrievalRetryTriggered,
    RagRetrievalSkipped,
)
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils.rag_client import make_async_client, resolve_rag_base_url
from universal_logging import get_logger

from services.rag.chunk_filters import chunk_is_junk
from services.rag.metadata_boost import apply_metadata_boost

from .context_formatting import ChunkData, format_context
from .retrieval_execution import RetrievedChunk as _RetrievedChunk
from .retrieval_execution import execute_single_query as _execute_single_query
from .retrieval_execution import expand_neighbors as _expand_neighbors
from .retrieval_execution import rrf_merge as _rrf_merge
from .retrieval_profiles import load_retrieval_profiles, resolve_retrieval_params

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class RagMultiRetrieveHandler(BaseHandler):
    """
    Multi-query RAG retrieval with RRF merge.

    Reads structured JSON from an upstream query-rewriting step,
    executes parallel RAG searches for each rewritten query,
    merges via reciprocal rank fusion, and returns formatted context.

    Tunable resolution per request:
        runtime pipeline_options  >  profile[consumer_model]  >  scope_defaults[scope]  >  YAML defaults

    Options (via pipeline_options or YAML defaults):
        rag_top_k_per_query, rag_max_chunks, rag_rrf_k, rag_recency_weight,
        scope_confidence_threshold, scope_override, rag_source_prefixes,
        bibliography_filter_threshold, bibliography_filter_disable,
        consumer_model (optional — triggers profile lookup from retrieval-profiles.yaml;
        can be None if no matching profile is found).

    Scope-based retrieval:
        - The rewrite step predicts a scope label (e.g., "research", "project")
        - The handler sends the scope to the RAG /search endpoint via scope= param
        - The RAG service resolves the scope to source prefixes using its config
        - Explicit rag_source_prefixes in pipeline_options bypasses scope resolution

    Domain fields (from pipeline YAML step config):
        endpoint: str — RAG service URL

    handler_inputs:
        rewrite_result — bound to upstream step's .json output
    """

    step_type: str = "rag_multi_retrieve_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Execute multi-query RAG retrieval with RRF merge and optional retry.

        Orchestrates retrieval from rewritten queries, merges results via
        reciprocal rank fusion, applies junk filtering and metadata boost,
        and emits retrieval lifecycle events.
        """
        endpoint: str = step.get_domain_field("endpoint", "")
        if not endpoint:
            raise ValueError(f"Step '{step.id}': missing 'endpoint' domain field")

        socket_path: str | None = step.get_domain_field("socket_path")
        if socket_path:
            base_url = (
                socket_path
                if socket_path.startswith("unix://")
                else f"unix://{socket_path}"
            )
        else:
            base_url = resolve_rag_base_url()
        parsed_endpoint = urlparse(endpoint)
        api_path = f"/{(parsed_endpoint.path or '/search').strip('/') or 'search'}"

        resolver = NamespaceResolver(context)
        rewrite_data: dict[str, Any] = self._resolve_input(
            resolver, step, "rewrite_result", step.handler_inputs
        )

        if not isinstance(rewrite_data, dict):
            logger.warning(
                "Step '%s': rewrite_result is %s, expected dict — falling back",
                step.id,
                type(rewrite_data).__name__,
            )
            rewrite_data = {"needs_retrieval": True, "scope": "both"}

        if not rewrite_data.get("needs_retrieval", True):
            logger.info("Step '%s': needs_retrieval=false, skipping RAG", step.id)
            return StepOutput(
                raw=_NO_RETRIEVAL_SENTINEL,
                json={"chunks_found": 0, "queries_executed": 0, "chunks": []},
            )

        out_of_scope_reason: str = rewrite_data.get("out_of_scope_reason", "")
        user_prefix_override = isinstance(
            context.runtime_options.get("rag_source_prefixes"), list
        )
        if out_of_scope_reason and not user_prefix_override:
            logger.info(
                "Step '%s': out_of_scope_reason='%s' — skipping retrieval "
                "(no user prefix override)",
                step.id,
                out_of_scope_reason,
            )
            self._publish_bus_event(
                context,
                RagRetrievalSkipped(
                    pipeline_id=context.pipeline.id,
                    execution_id=context.execution_id,
                    step_name=step.name,
                    reason="out_of_scope",
                    out_of_scope_reason=out_of_scope_reason,
                ),
            )
            return StepOutput(
                raw=f"[RAG-OUT-OF-SCOPE: {out_of_scope_reason}]",
                json={
                    "chunks_found": 0,
                    "queries_executed": 0,
                    "out_of_scope": True,
                    "out_of_scope_reason": out_of_scope_reason,
                    "chunks": [],
                },
            )

        queries: list[str] = rewrite_data.get("rewritten_queries", [])
        if not queries:
            queries = [context.source_text]
            logger.info(
                "Step '%s': no rewritten queries, falling back to source text",
                step.id,
            )
        elif context.source_text not in queries:
            queries.insert(0, context.source_text)

        # --- HyDE passage ---
        hyde_passage: str = rewrite_data.get("hyde_passage", "")

        # --- Multi-layer tunable resolution ---
        yaml_defaults = context.pipeline.options.to_context_dict()
        runtime = context.runtime_options
        profiles_data = load_retrieval_profiles()

        params = resolve_retrieval_params(
            yaml_defaults=yaml_defaults,
            runtime=runtime,
            profiles_data=profiles_data,
            step_id=step.id,
        )
        consumer_tier = params.consumer_tier
        consumer_model = params.consumer_model
        matched_class_name = params.matched_class_name
        effective = params.effective
        top_k = params.top_k
        max_chunks = params.max_chunks
        rrf_k = params.rrf_k
        recency_weight = params.recency_weight
        confidence_threshold = params.confidence_threshold

        # --- Scope resolution ---
        explicit_prefixes_raw = effective.get("rag_source_prefixes")
        source_prefixes: list[str] | None = None
        scope: str | list[str]
        search_scope: str | list[str] | None
        if isinstance(explicit_prefixes_raw, list) and all(
            isinstance(x, str) for x in explicit_prefixes_raw
        ):
            source_prefixes = explicit_prefixes_raw
            scope = "custom"
            search_scope = None  # use raw prefixes
            retrieval_mode = "source_prefixes"
        else:
            if explicit_prefixes_raw is not None:
                logger.warning(
                    "Step '%s': 'rag_source_prefixes' is not a list of strings, ignoring.",
                    step.id,
                )
            scope_override_raw = effective.get("scope_override", "")
            if isinstance(scope_override_raw, list) and len(scope_override_raw) > 0:
                scope = scope_override_raw
                valid_scopes: set[str] = set(
                    effective.get("valid_scopes")
                    or context.pipeline.options.to_context_dict().get("valid_scopes")
                    or []
                )
                if valid_scopes and not all(s in valid_scopes for s in scope):
                    logger.info(
                        "Step '%s': scope_override %s not all in valid_scopes, "
                        "fallback to ['both']",
                        step.id,
                        scope,
                    )
                    scope = ["both"]
                source_prefixes = None
                search_scope = scope
                retrieval_mode = "scope"
            elif isinstance(scope_override_raw, str) and scope_override_raw.strip():
                scope = scope_override_raw.strip()
                source_prefixes = None
                search_scope = scope
                retrieval_mode = "scope"
            else:
                predicted_scope = rewrite_data.get("scope", "both")
                _pipeline_options = context.pipeline.options.to_context_dict()
                scope_aliases: dict[str, str] = (
                    effective.get("scope_aliases") or _pipeline_options.get("scope_aliases") or {}
                )
                resolved_scope = scope_aliases.get(predicted_scope, predicted_scope)
                if resolved_scope != predicted_scope:
                    logger.info(
                        "Step '%s': scope alias '%s' → '%s'",
                        step.id,
                        predicted_scope,
                        resolved_scope,
                    )
                # TODO: derive from single source — e.g. RAG config scopes or pipeline
                # scopes map — instead of a separate hardcoded valid_scopes list
                valid_scopes = set(
                    effective.get("valid_scopes")
                    or _pipeline_options.get("valid_scopes")
                    or []
                )
                default_scope = "both"
                if valid_scopes and resolved_scope not in valid_scopes:
                    logger.info(
                        "Step '%s': scope '%s' not in valid_scopes, fallback to '%s'",
                        step.id,
                        resolved_scope,
                        default_scope,
                    )
                    resolved_scope = default_scope
                scope_confidence = float(rewrite_data.get("scope_confidence", 1.0))
                scope = (
                    resolved_scope
                    if scope_confidence >= confidence_threshold
                    else "both"
                )
                if scope != resolved_scope:
                    logger.info(
                        "Step '%s': scope_confidence=%.2f < threshold=%.2f, "
                        "overriding scope '%s' → 'both'",
                        step.id,
                        scope_confidence,
                        confidence_threshold,
                        resolved_scope,
                    )
                source_prefixes = None
                search_scope = scope
                retrieval_mode = "scope"

        scope_key = scope[0] if isinstance(scope, list) and scope else scope

        # Tier 3: scope-conditional recency (unless caller explicitly overrode it)
        if "rag_recency_weight" not in runtime:
            scope_recency = (
                profiles_data.get("scope_defaults", {})
                .get(scope_key, {})
                .get("rag_recency_weight")
            )
            if scope_recency is not None:
                recency_weight = float(scope_recency)

        # --- Append HyDE passage as additional retrieval query ---
        hyde_enabled: bool = bool(effective.get("hyde_enabled", True))
        if hyde_enabled and hyde_passage.strip():
            queries.append(hyde_passage)
            logger.info(
                "Step '%s': HyDE passage appended (%d chars)",
                step.id,
                len(hyde_passage),
            )

        logger.info(
            "Step '%s': executing %d queries (scope=%s, top_k=%d, rrf_k=%d)",
            step.id,
            len(queries),
            scope,
            top_k,
            rrf_k,
        )

        self._publish_bus_event(
            context,
            RagRetrievalParamsResolved(
                pipeline_id=context.pipeline.id,
                execution_id=context.execution_id,
                step_name=step.name,
                consumer_model=consumer_model or None,
                consumer_tier=consumer_tier,
                profile_class=matched_class_name,
                max_chunks=max_chunks,
                top_k_per_query=top_k,
                rrf_k=rrf_k,
                scope=scope,
                retrieval_mode=retrieval_mode,
                uses_explicit_prefixes=bool(source_prefixes),
            ),
        )
        try:
            rag_timeout = float(effective.get("rag_client_timeout", 30.0))
        except (TypeError, ValueError):
            logger.warning(
                "Step '%s': 'rag_client_timeout' is invalid, using default 30.0 seconds.",
                step.id,
            )
            rag_timeout = 30.0
        _retrieval_start = _time.monotonic()

        async with make_async_client(base_url, timeout=rag_timeout) as client:
            tasks = [
                _execute_single_query(
                    client,
                    api_path,
                    q,
                    top_k,
                    recency_weight,
                    search_scope,
                    source_prefixes,
                )
                for q in queries
            ]
            results_per_query = await asyncio.gather(*tasks, return_exceptions=True)

        successful: list[list[_RetrievedChunk]] = []
        for i, result in enumerate(results_per_query):
            if isinstance(result, BaseException):
                logger.error("Step '%s': query %d failed: %s", step.id, i, result)
            else:
                successful.append(result)

        if not successful:
            _retrieval_seconds = _time.monotonic() - _retrieval_start
            logger.warning("Step '%s': all queries failed", step.id)
            self._publish_bus_event(
                context,
                RagRetrievalFailed(
                    pipeline_id=context.pipeline.id,
                    execution_id=context.execution_id,
                    step_name=step.name,
                    error=f"all {len(queries)} queries failed",
                    total_retrieval_seconds=_retrieval_seconds,
                ),
            )
            return StepOutput(
                raw=_NO_RESULTS_SENTINEL,
                json={
                    "chunks_found": 0,
                    "queries_executed": len(queries),
                    "chunks": [],
                },
            )

        merged, merged_scores = _rrf_merge(successful, k=rrf_k, max_chunks=max_chunks)

        # --- Low-chunk retry with broader scope (once) ---
        min_chunks_threshold = int(effective.get("rag_min_chunks_retry_threshold", 0))
        retry_fallback = (
            str(effective.get("rag_retry_scope_fallback", "both")).strip() or "both"
        )
        if (
            min_chunks_threshold > 0
            and len(merged) < min_chunks_threshold
            and scope_key not in (retry_fallback, "all")
            and source_prefixes is None
        ):
            prev_count = len(merged)
            self._publish_bus_event(
                context,
                RagRetrievalRetryTriggered(
                    pipeline_id=context.pipeline.id,
                    execution_id=context.execution_id,
                    step_name=step.name,
                    initial_chunk_count=prev_count,
                    threshold=min_chunks_threshold,
                    retry_scope=retry_fallback,
                    reason="low_chunk_count",
                ),
            )
            logger.info(
                "Step '%s': chunks=%d < threshold=%d, retrying with scope=%r",
                step.id,
                prev_count,
                min_chunks_threshold,
                retry_fallback,
            )
            retry_scope = retry_fallback
            async with make_async_client(base_url, timeout=rag_timeout) as client_retry:
                tasks_retry = [
                    _execute_single_query(
                        client_retry,
                        api_path,
                        q,
                        top_k,
                        recency_weight,
                        retry_scope,
                        None,
                    )
                    for q in queries
                ]
                results_retry = await asyncio.gather(
                    *tasks_retry, return_exceptions=True
                )
            successful_retry = [
                r for r in results_retry if not isinstance(r, BaseException)
            ]
            if successful_retry:
                merged_retry, merged_scores_retry = _rrf_merge(
                    successful_retry, k=rrf_k, max_chunks=max_chunks
                )
                if len(merged_retry) > prev_count:
                    merged = merged_retry
                    merged_scores = merged_scores_retry
                    scope = retry_scope
                    logger.info(
                        "Step '%s': retry with scope=%r yielded %d chunks (was %d)",
                        step.id,
                        retry_scope,
                        len(merged),
                        prev_count,
                    )
                    self._publish_bus_event(
                        context,
                        RagRetrievalRetrySucceeded(
                            pipeline_id=context.pipeline.id,
                            execution_id=context.execution_id,
                            step_name=step.name,
                            initial_chunk_count=prev_count,
                            final_chunk_count=len(merged),
                            retry_scope=retry_scope,
                        ),
                    )
                else:
                    self._publish_bus_event(
                        context,
                        RagRetrievalRetryNotImproved(
                            pipeline_id=context.pipeline.id,
                            execution_id=context.execution_id,
                            step_name=step.name,
                            initial_chunk_count=prev_count,
                            final_chunk_count=len(merged_retry),
                            retry_scope=retry_scope,
                        ),
                    )

        # --- Post-RRF junk filter ---
        if effective.get("bibliography_filter_disable", False):
            junk_threshold = 0.0
        else:
            _raw_threshold = effective.get("bibliography_filter_threshold", 0.35)
            try:
                junk_threshold = float(_raw_threshold)
            except (TypeError, ValueError):
                logger.warning(
                    "Step '%s': bibliography_filter_threshold=%r invalid, using 0.35",
                    step.id,
                    _raw_threshold,
                )
                junk_threshold = 0.35
        pre_junk = len(merged)
        clean: list[_RetrievedChunk] = []
        if junk_threshold <= 0:
            clean = merged
        else:
            for chunk in merged:
                if chunk.metadata.get("is_bibliography") or chunk_is_junk(
                    chunk.content, threshold=junk_threshold
                ):
                    merged_scores.pop(chunk.content_hash, None)
                else:
                    clean.append(chunk)
        chunks_dropped = pre_junk - len(clean)
        if chunks_dropped > 0:
            logger.info(
                "Step '%s': junk filter removed %d/%d chunks",
                step.id,
                chunks_dropped,
                pre_junk,
            )
            self._publish_bus_event(
                context,
                RagRetrievalBibliographyFiltered(
                    pipeline_id=context.pipeline.id,
                    execution_id=context.execution_id,
                    step_name=step.name,
                    chunks_dropped=chunks_dropped,
                ),
            )
        merged = clean

        # --- Neighbor expansion (post-junk-filter, pre-metadata-boost) ---
        neighbor_enabled = bool(effective.get("neighbor_expansion_enabled", False))
        neighbor_expansion_added = 0
        if neighbor_enabled:
            neighbor_n = int(effective.get("neighbor_expansion_n", 1))
            neighbor_max = int(effective.get("neighbor_expansion_max_chunks", 30))
            neighbor_discount = float(
                effective.get("neighbor_expansion_score_discount", 1.0)
            )
            _expansion_start = _time.monotonic()
            async with make_async_client(base_url, timeout=rag_timeout) as exp_client:
                expansion_result = await _expand_neighbors(
                    exp_client,
                    api_path,
                    merged,
                    merged_scores,
                    n=neighbor_n,
                    max_chunks=neighbor_max,
                    score_discount=neighbor_discount,
                )
            merged = expansion_result.chunks
            merged_scores = expansion_result.scores
            _expansion_seconds = _time.monotonic() - _expansion_start
            neighbor_expansion_added = expansion_result.neighbors_added
            self._publish_bus_event(
                context,
                RagNeighborExpansionCompleted(
                    pipeline_id=context.pipeline.id,
                    execution_id=context.execution_id,
                    step_name=step.name,
                    enabled=True,
                    neighbors_added=expansion_result.neighbors_added,
                    neighbors_fetched=expansion_result.neighbors_fetched,
                    sources_expanded=expansion_result.sources_expanded,
                    expansion_n=neighbor_n,
                    max_chunks=neighbor_max,
                    expansion_seconds=_expansion_seconds,
                ),
            )
            if expansion_result.neighbors_added > 0:
                logger.info(
                    "Step '%s': neighbor expansion added %d chunks "
                    "(fetched=%d, sources=%d, %.2fs)",
                    step.id,
                    expansion_result.neighbors_added,
                    expansion_result.neighbors_fetched,
                    expansion_result.sources_expanded,
                    _expansion_seconds,
                )

        # --- Post-RRF metadata boost ---
        boost_enabled = bool(effective.get("metadata_boost_enabled", True))
        boost_weight = float(effective.get("metadata_boost_weight", 0.20))
        coverage_enabled = bool(effective.get("coverage_selection_enabled", False))

        boost_result = apply_metadata_boost(
            chunks=merged,
            rrf_scores=merged_scores,
            original_query=context.source_text,
            rewritten_queries=rewrite_data.get("rewritten_queries", []),
            enabled=boost_enabled,
            weight=boost_weight,
            coverage_enabled=coverage_enabled,
            max_chunks=max_chunks,
        )
        merged = boost_result.chunks
        merged_scores = boost_result.scores

        self._publish_bus_event(
            context,
            RagMetadataBoostApplied(
                pipeline_id=context.pipeline.id,
                execution_id=context.execution_id,
                step_name=step.name,
                metadata_hit_count=boost_result.metadata_hit_count,
                avg_metadata_score=boost_result.avg_metadata_score,
                applied=boost_result.applied,
                chunks_after_boost=len(merged),
            ),
        )

        chunk_dicts: list[ChunkData] = [
            {
                "content": c.content,
                "source": c.source,
                "indexed_at": c.indexed_at,
                "metadata": c.metadata,
                "content_hash": c.content_hash,
                "score": boost_result.scores.get(c.content_hash, 0.0),
            }
            for c in merged
        ]
        context_text = format_context(chunk_dicts)

        total_raw = sum(len(r) for r in successful)
        logger.info(
            "Step '%s': retrieved %d raw chunks → %d after RRF merge",
            step.id,
            total_raw,
            len(merged),
        )
        _retrieval_seconds = _time.monotonic() - _retrieval_start
        chunks_per_query = [len(r) for r in successful]
        rrf_scores_list = list(merged_scores.values())
        predicted_scope_for_event = str(rewrite_data.get("scope", "unknown"))
        fallback_triggered = scope_key != predicted_scope_for_event
        rrf_score_min = min(rrf_scores_list) if rrf_scores_list else 0.0
        rrf_score_max = max(rrf_scores_list) if rrf_scores_list else 0.0
        rrf_score_mean = (
            sum(rrf_scores_list) / len(rrf_scores_list) if rrf_scores_list else 0.0
        )
        self._publish_bus_event(
            context,
            RagRetrievalCompleted(
                pipeline_id=context.pipeline.id,
                execution_id=context.execution_id,
                step_name=step.name,
                predicted_scope=predicted_scope_for_event,
                scope_confidence=float(rewrite_data.get("scope_confidence", 1.0)),
                fallback_triggered=fallback_triggered,
                chunks_per_query=chunks_per_query,
                zero_result_queries=sum(1 for c in chunks_per_query if c == 0),
                rrf_score_min=rrf_score_min,
                rrf_score_max=rrf_score_max,
                rrf_score_mean=rrf_score_mean,
                chunks_after_merge=len(merged),
                total_retrieval_seconds=_retrieval_seconds,
                neighbor_expansion_added=neighbor_expansion_added,
            ),
        )

        return StepOutput(
            raw=context_text,
            json={
                "chunks_found": len(merged),
                "queries_executed": len(queries),
                "queries_succeeded": len(successful),
                "raw_chunks_total": total_raw,
                "scope": scope,
                "rewritten_queries": queries,
                "chunks": chunk_dicts,
                "effective_params": {
                    "top_k_per_query": top_k,
                    "max_chunks": max_chunks,
                    "rrf_k": rrf_k,
                    "recency_weight": recency_weight,
                    "scope_confidence_threshold": confidence_threshold,
                    "consumer_model": consumer_model or None,
                    "consumer_tier": consumer_tier,
                    "profile_applied": bool(
                        consumer_model and params.exact_model_profile
                    ),
                    "tier_applied": bool(consumer_tier and params.tier_profile),
                },
            },
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        """Validate step config: endpoint and rewrite_result handler_input required."""
        errors: list[str] = []
        if not step.get_domain_field("endpoint"):
            errors.append(f"Step '{step.id}' missing required 'endpoint' field")
        if not step.handler_inputs or "rewrite_result" not in step.handler_inputs:
            errors.append(
                f"Step '{step.id}' missing 'rewrite_result' in handler_inputs"
            )
        return errors
