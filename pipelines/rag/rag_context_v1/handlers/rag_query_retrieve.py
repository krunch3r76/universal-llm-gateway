"""
Two-pool RAG retrieval with RRF, source habituation, Pool B source swap, and metadata boost.

Zero LLM calls on the default path (~1.6 s end-to-end including cross-encoder reranking).

Architecture
------------
Pool A — broad semantic retrieval
    Dense embeddings + BM25 sidecar, merged via mini-RRF inside the RAG service for
    each query.  Finds text related to the *idea* even when query vocabulary differs
    from indexed text.  Multiple Pool A queries (original + rewritten variants) are
    merged by a second RRF pass in ``retrieval_execution.rrf_merge``.

Pool B — exact-match sparse retrieval (``sparse_only=True``)
    FTS5 full-text search with BM25 ranking; no embedding model.  The query is
    factored into sub-phrases (phrase extraction) and augmented with corpus-derived
    co-occurrence terms (IDF expansion) — see ``term_expansion.py``.  Each facet is
    dispatched as its own keyword query, catching identifiers and technical terms that
    dense search blurs.  Pool A and Pool B results are merged by the same RRF pass.

Post-merge scoring (applied in this order after the combined RRF merge)
-----------------------------------------------------------------------
1. **Metadata boost** — entity/topic/relation overlap between query and chunk
   metadata adjusts scores before the context window is trimmed.  Weight default
   0.20; formula in ``metadata_boost.py``.

2. **Lateral source habituation (Pool B)** — within each source's Pool B hits, the
   top-ranked chunk receives a score boost (×``facet_pool_score_boost``, default
   ×1.5); subsequent hits from the same source are penalised (×1/boost).  Prevents
   any single source from monopolising boosted Pool B slots.

3. **Global source habituation** — after sorting, each additional chunk from an
   already-represented source is penalised exponentially
   (``score /= source_habituation_factor ** n``, n ≥ 1, 0-indexed).  Pushes
   ranking toward new sources; applies to Pool A and Pool B alike.

4. **Pool B source swap** — when a source has Pool B (exact-match) hits, Pool A
   (semantic) chunks from that source are candidates for eviction.  Binary mode
   (``facet_pool_swap_distance_threshold=0.0``, default): evict all Pool A
   chunks.  Graduated mode (threshold > 0): retain up to
   ``facet_pool_swap_max_retain`` Pool A chunks whose cosine distance from
   every Pool B hit exceeds the threshold — they cover genuinely different
   content.  Context budget saved by eviction is redirected to sources not yet
   represented.  Disabled entirely on the ``general`` retrieval path.

All three diversity mechanisms (habituation, source cap, swap) are controlled by
the ``retrieval_path`` option.  ``research`` (default) enables all three with
aggressive settings.  ``general`` disables all three — pure relevance ranking.
For project documentation, architecture specs, and any corpus where a single
authoritative source is expected, callers set ``retrieval_path: general`` rather
than tuning individual diversity parameters.

Observed dynamics (not formally measured — from pipeline trace analysis):
- Pool overlap is routine: IDF expansion reliably surfaces the same prominent
  sources Pool A finds via dense similarity. Overlap is the expected steady
  state, not an edge case.
- For research papers (focused, single-topic documents), binary eviction is
  safe — when both pools converge on the same paper, they land on the same
  core content. Graduated mode rarely changes the outcome.
- For long multi-topic documents (design specs, architecture files), both pools
  converge on the same file but via different sections. Graduated mode retains
  Pool A chunks covering sections Pool B didn't reach.
- Corpus expansion is the primary source-diversity mechanism. The swap is a
  low-activity safety net when expansion is effective — it earns its keep by
  freeing slots for sources Pool B reached that Pool A never found.

Tunable resolution (per-request):
    ``retrieval_path`` preset (lowest priority among explicit settings) →
    ``runtime`` ∪ ``exact_model_profile`` ∪ ``model_class_profile`` ∪ ``tier_profile``
    ∪ ``yaml_defaults`` (standard merge order in ``resolve_retrieval_params``), then —
    after retrieval scope is resolved — ``scope_defaults[scope_key]`` fills any key
    not set by those four profile layers or ``runtime``.  Explicit per-request keys
    always override the path preset.

Profiles loaded from ``pipelines/rag/retrieval-profiles.yaml`` (cached after first load).

Scope validation:
    Scope authority derives from the RAG service scope registry (``GET /scopes``),
    not a static pipeline-local list.  Invalid or low-confidence scopes result in
    fail-closed behavior (0 chunks returned), never implicit broadening.

Invariants:
- ∀ execute(): returns StepOutput.raw = formatted context text (never empty string)
- ∀ needs_retrieval=false: returns sentinel (generation step handles gracefully)
- ∀ RRF merge: deduplicates by chunk content hash, scores by rank only
- ∀ invalid/unknown scope: 0 chunks (¬fallback broadening)
- ∀ low-confidence scope: 0 chunks (¬implicit broadening)
"""

from __future__ import annotations

import asyncio
import time as _time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, override
from urllib.parse import urlparse

from systems.pipeline.core.constants import (
    RAG_NO_RESULTS_SENTINEL as _NO_RESULTS_SENTINEL,
)
from systems.pipeline.core.constants import (
    RAG_NO_RETRIEVAL_SENTINEL as _NO_RETRIEVAL_SENTINEL,
)
from systems.pipeline.core.events.step import (
    RagCoverageSelectionApplied,
    RagMetadataBoostApplied,
    RagNeighborExpansionApplied,
    RagQueryAnalysisCompleted,
    RagQueryRewriteCompleted,
    RagQueryRewriteSkipped,
    RagRetrievalBibliographyFiltered,
    RagRetrievalCompleted,
    RagRetrievalFailed,
    RagRetrievalParamsResolved,
    RagRetrievalSkipped,
    RagRetrievalSourceDiversityLimited,
    RagScopeRejected,
)
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import make_async_client, resolve_rag_base_url
from universal_logging import get_logger

from services.rag.chunk_filters import chunk_is_noise, chunk_metadata_is_noise
from services.rag.metadata_boost import apply_metadata_boost

from .context_formatting import ChunkData, format_context
from .retrieval_execution import RetrievedChunk as _RetrievedChunk
from .retrieval_execution import build_facet_pool as _build_facet_pool
from .retrieval_execution import (
    compute_and_dispatch_pool_b as _compute_and_dispatch_pool_b,
)
from .retrieval_execution import execute_single_query as _execute_single_query
from .retrieval_execution import expand_neighbors as _expand_neighbors
from .retrieval_execution import (
    graduated_pool_b_swap as _graduated_pool_b_swap,
)
from .retrieval_execution import rrf_merge as _rrf_merge
from .retrieval_profiles import load_retrieval_profiles, resolve_retrieval_params
from .scope_catalog import (
    fetch_scope_prefixes,
    fetch_valid_scopes,
    resolve_child_scopes,
)

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_RETRIEVAL_PATH_PRESETS: dict[str, dict[str, Any]] = {
    "research": {
        "source_diversity_max": 3,
        "source_habituation_factor": 1.5,
        "facet_pool_swap_enabled": True,
    },
    "general": {
        "source_diversity_max": 0,
        "source_habituation_factor": 1.0,
        "facet_pool_swap_enabled": False,
    },
}


def _noise_filter_disable_effective(effective: dict[str, Any]) -> bool:
    v = effective.get("noise_filter_disable")
    if v is not None:
        return bool(v)
    return bool(effective.get("bibliography_filter_disable", False))


def _noise_filter_threshold_effective(
    effective: dict[str, Any], *, step_id: str
) -> float:
    raw = effective.get("noise_filter_threshold")
    key = "noise_filter_threshold"
    if raw is None:
        raw = effective.get("bibliography_filter_threshold", 0.35)
        key = "bibliography_filter_threshold"
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Step '%s': %s=%r invalid, using 0.35",
            step_id,
            key,
            raw,
        )
        return 0.35


class RagMultiRetrieveHandler(BaseHandler):
    """
    Multi-query RAG retrieval with RRF merge.

    Reads structured JSON from split scope-analysis, rewrite, and HyDE steps,
    executes parallel RAG searches for rewritten and HyDE queries,
    merges via reciprocal rank fusion, and returns formatted context.

    Tunable resolution per request:
        resolve_retrieval_params merge (runtime, exact/model_class/tier, yaml), then
        scope_defaults[scope_key] fills keys not set by runtime or any profile layer.

    Options (via pipeline_options or YAML defaults):
        rag_top_k_per_query, rag_max_chunks, rag_rrf_k, rag_recency_weight,
        scope_confidence_threshold, scope_override, rag_source_prefixes,
        noise_filter_threshold, noise_filter_disable (preferred);
        bibliography_filter_threshold, bibliography_filter_disable (aliases — same behavior),
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
        scope_result — required; bound to analyze_scope.json
        rewrite_result — optional; bound to generate_rewrites.json
        hyde_result — optional; bound to generate_hyde.json
    """

    step_type: str = "rag_multi_retrieve_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Executes multi-query RAG retrieval.

        Orchestrates retrieval from rewritten queries, merges results via
        reciprocal rank fusion, applies junk filtering and metadata boost,
        and emits retrieval lifecycle events.

        Returns:
            StepOutput where:
            - raw: formatted context text, or a non-empty sentinel when no context
            - json.chunks_found: final chunk count after filters/boost
            - json.queries_executed: number of queries dispatched
            - json.queries_succeeded: successful query count
            - json.raw_chunks_total: total chunks across successful queries
            - json.scope: resolved retrieval scope
            - json.rewritten_queries: effective query list (with source text)
            - json.chunks: formatted chunk dictionaries used for assembly
            - json.effective_params: resolved retrieval tunables for observability
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
        scope_data_raw = self._resolve_input(
            resolver, step, "scope_result", step.handler_inputs
        )
        if not isinstance(scope_data_raw, dict):
            raise ValueError(
                f"Step '{step.id}': scope_result must be dict, got "
                f"{type(scope_data_raw).__name__}"
            )
        scope_data: dict[str, Any] = scope_data_raw

        def _is_valid_step_result(data: Any) -> bool:
            return isinstance(data, dict) and not bool(data.get("_skipped", False))

        rewrite_data_raw = self._resolve_input(
            resolver, step, "rewrite_result", step.handler_inputs
        )
        rewrite_result: dict[str, Any] = (
            rewrite_data_raw if isinstance(rewrite_data_raw, dict) else {}
        )
        rewrite_result_valid = _is_valid_step_result(rewrite_result)
        hyde_data_raw = self._resolve_input(
            resolver, step, "hyde_result", step.handler_inputs
        )
        hyde_result: dict[str, Any] = (
            hyde_data_raw if isinstance(hyde_data_raw, dict) else {}
        )
        hyde_result_valid = _is_valid_step_result(hyde_result)

        # Pool B facets: either read from a prior step (rewrite pipeline) or
        # computed inline concurrently with pool A (direct pipeline).
        facet_pool: list[tuple[str, str]] = []  # (facet_label, or_query)
        _inline_facets: bool = False
        computed_facets: list[dict[str, object]] = []
        if step.handler_inputs and "facet_result" in step.handler_inputs:
            try:
                facet_raw = self._resolve_input(
                    resolver, step, "facet_result", step.handler_inputs
                )
                facet_pool = _build_facet_pool(facet_raw)
                if isinstance(facet_raw, list):
                    computed_facets = [f for f in facet_raw if isinstance(f, dict)]
            except Exception:
                logger.warning(
                    "Step '%s': failed to resolve facet_result, skipping pool B",
                    step.id,
                    exc_info=True,
                )
        else:
            _inline_facets = True
        raw_scopes = scope_data.get("scopes") or scope_data.get("scope", "all")
        if isinstance(raw_scopes, list):
            predicted_scopes = [
                s for s in raw_scopes if isinstance(s, str) and s.strip()
            ]
            if not predicted_scopes:
                predicted_scopes = ["all"]
        elif isinstance(raw_scopes, str) and raw_scopes.strip():
            predicted_scopes = [raw_scopes]
        else:
            predicted_scopes = ["all"]

        rewrite_data: dict[str, Any] = {
            "needs_retrieval": bool(scope_data.get("needs_retrieval", True)),
            "scopes": predicted_scopes,
            "scope_confidence": float(scope_data.get("scope_confidence", 1.0)),
            "out_of_scope_reason": str(scope_data.get("out_of_scope_reason", "")),
            "rewritten_queries": rewrite_result.get("rewritten_queries", [])
            if rewrite_result_valid
            else [],
            "hyde_passage": hyde_result.get("hyde_passage", "")
            if hyde_result_valid
            else "",
        }

        rewrite_enabled = bool(
            context.runtime_options.get(
                "rewrite_enabled",
                context.pipeline.options.to_context_dict().get("rewrite_enabled", True),
            )
        )

        needs_retrieval = bool(rewrite_data.get("needs_retrieval", True))
        analysis_scope = rewrite_data.get("scopes", ["unknown"])
        analysis_scope_value = (
            analysis_scope[0]
            if isinstance(analysis_scope, list) and analysis_scope
            else "unknown"
        )
        self._publish_bus_event(
            context,
            RagQueryAnalysisCompleted(
                pipeline_id=context.pipeline.id,
                execution_id=context.execution_id,
                step_name=step.name,
                needs_retrieval=needs_retrieval,
                scope=analysis_scope_value,
                scope_confidence=float(rewrite_data.get("scope_confidence", 1.0)),
                out_of_scope_reason=str(rewrite_data.get("out_of_scope_reason", "")),
            ),
        )
        if rewrite_result_valid:
            rewritten_queries = rewrite_data.get("rewritten_queries", [])
            self._publish_bus_event(
                context,
                RagQueryRewriteCompleted(
                    pipeline_id=context.pipeline.id,
                    execution_id=context.execution_id,
                    step_name=step.name,
                    rewrite_count=(
                        len(rewritten_queries)
                        if isinstance(rewritten_queries, list)
                        else 0
                    ),
                    hyde_present=bool(
                        str(rewrite_data.get("hyde_passage", "")).strip()
                    ),
                ),
            )
        else:
            if not needs_retrieval:
                rewrite_skip_reason = "needs_retrieval_false"
            elif not rewrite_enabled:
                rewrite_skip_reason = "rewrite_disabled"
            else:
                rewrite_skip_reason = "step_condition_false"
            self._publish_bus_event(
                context,
                RagQueryRewriteSkipped(
                    pipeline_id=context.pipeline.id,
                    execution_id=context.execution_id,
                    step_name=step.name,
                    reason=rewrite_skip_reason,
                ),
            )

        if not rewrite_data.get("needs_retrieval", True):
            logger.info("Step '%s': needs_retrieval=false, skipping RAG", step.id)
            self._publish_bus_event(
                context,
                RagRetrievalSkipped(
                    pipeline_id=context.pipeline.id,
                    execution_id=context.execution_id,
                    step_name=step.name,
                    reason="needs_retrieval_false",
                    out_of_scope_reason="",
                ),
            )
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
                raw=_NO_RETRIEVAL_SENTINEL,
                json={
                    "chunks_found": 0,
                    "queries_executed": 0,
                    "out_of_scope": True,
                    "out_of_scope_reason": out_of_scope_reason,
                    "chunks": [],
                },
            )

        queries: list[str] = []
        if rewrite_result_valid:
            queries_raw = rewrite_data.get("rewritten_queries", [])
            if isinstance(queries_raw, list):
                queries = [q for q in queries_raw if isinstance(q, str) and q.strip()]
        if not queries:
            logger.info(
                "Step '%s': rewrite unavailable or empty, using source text only",
                step.id,
            )
            queries = [context.source_text]
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

        # Optional per-scope caps for multi-scope retrieval. Example:
        # rag_scope_chunk_caps: {"graph_modeling": 8, "knowledge_systems": 4}
        scope_chunk_caps_raw = effective.get("rag_scope_chunk_caps")
        scope_chunk_caps: dict[str, int] = {}
        if isinstance(scope_chunk_caps_raw, dict):
            for scope_name, cap in scope_chunk_caps_raw.items():
                if not isinstance(scope_name, str):
                    continue
                try:
                    cap_int = int(cap)
                except (TypeError, ValueError):
                    continue
                if cap_int > 0:
                    scope_chunk_caps[scope_name] = cap_int

        # Synthetic scope: a caller-provided broad scope queried alongside
        # predicted leaf scopes but with score demotion. Distinct from
        # scope_override (which replaces prediction entirely).
        synthetic_scope_name = str(effective.get("synthetic_scope", "")).strip()
        try:
            synthetic_demotion = float(effective.get("synthetic_scope_demotion", 1.0))
        except (TypeError, ValueError):
            synthetic_demotion = 1.0

        # --- Scope resolution (fail-closed: invalid/uncertain → 0 chunks) ---
        explicit_prefixes_raw = effective.get("rag_source_prefixes")
        source_prefixes: list[str] | None = None
        scope: str | list[str]
        search_scope: str | list[str] | None
        if isinstance(explicit_prefixes_raw, list) and all(
            isinstance(x, str) for x in explicit_prefixes_raw
        ):
            source_prefixes = explicit_prefixes_raw
            scope = "custom"
            search_scope = None
            retrieval_mode = "source_prefixes"
        else:
            if explicit_prefixes_raw is not None:
                logger.warning(
                    "Step '%s': 'rag_source_prefixes' is not a list of strings, ignoring.",
                    step.id,
                )
            catalog = await fetch_valid_scopes(base_url)
            scope_override_raw = effective.get("scope_override", "")

            if isinstance(scope_override_raw, list) and len(scope_override_raw) > 0:
                scope = scope_override_raw
                if catalog is None:
                    return self._scope_rejection_output(
                        context,
                        step,
                        "scope_catalog_unavailable",
                        scope,
                        "RAG /scopes unreachable; fail-closed with explicit list override",
                    )
                invalid = [s for s in scope if s not in catalog]
                if invalid:
                    return self._scope_rejection_output(
                        context,
                        step,
                        "invalid_scope_override",
                        scope,
                        f"Unknown scope(s): {invalid}",
                    )
                source_prefixes = None
                search_scope = scope
                retrieval_mode = "scope"

            elif isinstance(scope_override_raw, str) and scope_override_raw.strip():
                scope = scope_override_raw.strip()
                if catalog is None:
                    return self._scope_rejection_output(
                        context,
                        step,
                        "scope_catalog_unavailable",
                        scope,
                        "RAG /scopes unreachable; fail-closed with explicit string override",
                    )
                if scope not in catalog:
                    return self._scope_rejection_output(
                        context,
                        step,
                        "invalid_scope_override",
                        scope,
                        f"Unknown scope: {scope!r}",
                    )
                source_prefixes = None
                search_scope = scope
                retrieval_mode = "scope"

            else:
                predicted_scopes: list[str] = rewrite_data.get("scopes", ["all"])
                _pipeline_options = context.pipeline.options.to_context_dict()
                scope_aliases: dict[str, str] = (
                    effective.get("scope_aliases")
                    or _pipeline_options.get("scope_aliases")
                    or {}
                )
                resolved_scopes: list[str] = []
                for ps in predicted_scopes:
                    rs = scope_aliases.get(ps, ps)
                    if rs != ps:
                        logger.info(
                            "Step '%s': scope alias '%s' → '%s'",
                            step.id,
                            ps,
                            rs,
                        )
                    resolved_scopes.append(rs)
                if catalog is None:
                    return self._scope_rejection_output(
                        context,
                        step,
                        "scope_catalog_unavailable",
                        resolved_scopes,
                        "RAG /scopes unreachable; fail-closed",
                    )
                invalid = [s for s in resolved_scopes if s not in catalog]
                if invalid:
                    valid = [s for s in resolved_scopes if s in catalog]
                    if not valid:
                        return self._scope_rejection_output(
                            context,
                            step,
                            "invalid_predicted_scope",
                            resolved_scopes,
                            f"No valid predicted scopes (invalid: {invalid})",
                        )
                    logger.warning(
                        "Step '%s': dropping invalid predicted scopes %s, keeping %s",
                        step.id,
                        invalid,
                        valid,
                    )
                    resolved_scopes = valid
                scope_confidence = float(rewrite_data.get("scope_confidence", 1.0))
                if scope_confidence < confidence_threshold:
                    return self._scope_rejection_output(
                        context,
                        step,
                        "scope_confidence_below_threshold",
                        resolved_scopes,
                        f"confidence={scope_confidence:.2f} < threshold={confidence_threshold:.2f}",
                    )
                # Extend with synthetic scope if provided and not already predicted.
                if (
                    synthetic_scope_name
                    and catalog
                    and synthetic_scope_name in catalog
                    and synthetic_scope_name not in resolved_scopes
                ):
                    resolved_scopes.append(synthetic_scope_name)
                    prefix_map = await fetch_scope_prefixes(base_url)
                    child_labels = (
                        resolve_child_scopes(
                            synthetic_scope_name, resolved_scopes, prefix_map
                        )
                        if prefix_map
                        else []
                    )
                    logger.info(
                        "Step '%s': synthetic scope '%s' appended "
                        "(demotion=%.2f, children=%s)",
                        step.id,
                        synthetic_scope_name,
                        synthetic_demotion,
                        child_labels,
                    )
                scope = (
                    resolved_scopes if len(resolved_scopes) > 1 else resolved_scopes[0]
                )
                source_prefixes = None
                search_scope = (
                    resolved_scopes if len(resolved_scopes) > 1 else resolved_scopes[0]
                )
                retrieval_mode = "scope"

        scope_key: str | None = scope if isinstance(scope, str) else None

        retrieval_path = str(effective.get("retrieval_path", "research")).strip()
        path_preset = _RETRIEVAL_PATH_PRESETS.get(retrieval_path, {})
        if retrieval_path and retrieval_path not in _RETRIEVAL_PATH_PRESETS:
            logger.warning(
                "Step '%s': unknown retrieval_path '%s', ignoring",
                step.id,
                retrieval_path,
            )
        for k, v in path_preset.items():
            if k not in runtime:
                effective[k] = v

        scope_profile_applied: dict[str, Any] = {}
        if scope_key is not None:
            raw_sd = profiles_data.get("scope_defaults", {})
            if isinstance(raw_sd, dict):
                scope_block = raw_sd.get(scope_key)
                if isinstance(scope_block, dict):
                    _profile_keys = (
                        set(runtime)
                        | set(params.tier_profile)
                        | set(params.model_class_profile)
                        | set(params.exact_model_profile)
                    )
                    for k, v in scope_block.items():
                        if k not in _profile_keys:
                            effective[k] = v
                            scope_profile_applied[k] = v

        params = replace(
            params,
            effective=effective,
            scope_profile=scope_profile_applied,
            top_k=max(1, int(effective.get("rag_top_k_per_query", 10))),
            max_chunks=max(1, int(effective.get("rag_max_chunks", 20))),
            rrf_k=max(1, int(effective.get("rag_rrf_k", 35))),
            recency_weight=float(effective.get("rag_recency_weight", 0.2)),
        )
        top_k = params.top_k
        max_chunks = params.max_chunks
        rrf_k = params.rrf_k
        recency_weight = params.recency_weight

        # Optional fixed-scope diagnostic mode: bypass rewrite expansion and
        # retrieve from source text only (keeps scope resolution unchanged).
        source_only_retrieval: bool = bool(
            effective.get("source_only_retrieval", False)
        )
        if source_only_retrieval:
            queries = [context.source_text]

        # --- Append HyDE passage as additional retrieval query ---
        hyde_enabled: bool = bool(effective.get("hyde_enabled", True))
        if source_only_retrieval or not rewrite_result_valid:
            hyde_enabled = False
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
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"Step '{step.id}': 'rag_client_timeout' is invalid: {e}. "
                f"Value was {effective.get('rag_client_timeout')!r}"
            ) from e
        _retrieval_start = _time.monotonic()
        chunk_scope_by_hash: dict[str, str] = {}
        chunk_all_scopes: dict[str, set[str]] = {}
        facet_chunk_hashes: set[str] = set()  # hashes sourced from pool B OR queries

        # Pool B top_k: smaller than pool A to avoid flooding RRF with sparse hits.
        try:
            facet_top_k = int(effective.get("facet_pool_top_k", 5))
        except (TypeError, ValueError):
            facet_top_k = 5

        async with make_async_client(base_url, timeout=rag_timeout) as client:
            results_per_query: list[list[_RetrievedChunk] | BaseException]
            # ── Batch-embed all dense queries in one GPU forward pass ──
            # Pool A queries need embeddings; pool B (sparse_only) does not.
            _query_embeddings: dict[int, list[float]] = {}
            if queries:
                _embed_start = _time.monotonic()
                try:
                    embed_resp = await client.post(
                        api_path.replace("/search", "/embed_batch"),
                        json={
                            "texts": queries,
                            "scope": search_scope
                            if isinstance(search_scope, str | list)
                            else None,
                        },
                    )
                    embed_resp.raise_for_status()
                    embed_data = embed_resp.json()
                    for idx, emb in enumerate(embed_data.get("embeddings", [])):
                        _query_embeddings[idx] = emb
                    logger.info(
                        "Step '%s': batch-embedded %d queries in %.3fs",
                        step.id,
                        len(queries),
                        _time.monotonic() - _embed_start,
                    )
                except Exception:
                    logger.warning(
                        "Step '%s': batch embedding failed, falling back to "
                        "per-query embedding",
                        step.id,
                        exc_info=True,
                    )

            # ── Inline facet config (when computed concurrently with pool A) ──
            _idf_max_phrases: int = int(effective.get("max_expansion_terms", 10))
            _idf_max_terms: int = int(effective.get("max_idf_terms", 8))
            _idf_max_disc: int = int(effective.get("max_discriminative", 4))

            _is_multi_scope = (
                isinstance(search_scope, list)
                and len(search_scope) > 1
                and source_prefixes is None
            )

            if _is_multi_scope:
                task_specs: list[tuple[int, str]] = []
                pool_a_tasks: list[Any] = []
                for q_idx, q in enumerate(queries):
                    for scoped_label in search_scope:
                        task_specs.append((q_idx, scoped_label))
                        pool_a_tasks.append(
                            _execute_single_query(
                                client,
                                api_path,
                                q,
                                top_k,
                                recency_weight,
                                scoped_label,
                                None,
                                query_embedding=_query_embeddings.get(q_idx),
                            )
                        )

                if _inline_facets:
                    # Concurrent: pool A + IDF/phrase computation → pool B
                    (
                        pool_a_raw,
                        (computed_facets, facet_pool, pool_b_raw),
                    ) = await asyncio.gather(
                        asyncio.gather(*pool_a_tasks, return_exceptions=True),
                        _compute_and_dispatch_pool_b(
                            client,
                            api_path,
                            context.source_text,
                            facet_top_k,
                            recency_weight,
                            None,
                            None,
                            max_phrases=_idf_max_phrases,
                            max_idf_terms=_idf_max_terms,
                            max_discriminative=_idf_max_disc,
                            multi_scope_labels=list(search_scope),
                        ),
                    )
                    # Rebuild task_specs to include pool B entries
                    facet_q_offset = len(queries)
                    for f_idx, _ in enumerate(facet_pool):
                        for scoped_label in search_scope:
                            task_specs.append((facet_q_offset + f_idx, scoped_label))
                    task_results = list(pool_a_raw) + pool_b_raw
                else:
                    facet_q_offset = len(queries)
                    for f_idx, (_, or_query) in enumerate(facet_pool):
                        for scoped_label in search_scope:
                            task_specs.append((facet_q_offset + f_idx, scoped_label))
                            pool_a_tasks.append(
                                _execute_single_query(
                                    client,
                                    api_path,
                                    or_query,
                                    facet_top_k,
                                    recency_weight,
                                    scoped_label,
                                    None,
                                    sparse_only=True,
                                )
                            )
                    task_results = await asyncio.gather(
                        *pool_a_tasks, return_exceptions=True
                    )

                if facet_pool:
                    logger.info(
                        "Step '%s': pool B — %d facet sparse queries "
                        "(top_k=%d, inline=%s): %s",
                        step.id,
                        len(facet_pool),
                        facet_top_k,
                        _inline_facets,
                        [label for label, _ in facet_pool],
                    )

                per_query_chunks: dict[int, list[_RetrievedChunk]] = {}
                per_query_errors: dict[int, BaseException] = {}
                for (q_idx, scoped_label), result in zip(
                    task_specs, task_results, strict=True
                ):
                    if isinstance(result, BaseException):
                        per_query_errors.setdefault(q_idx, result)
                        continue
                    for chunk in result:
                        chunk_scope_by_hash.setdefault(chunk.content_hash, scoped_label)
                        chunk_all_scopes.setdefault(chunk.content_hash, set()).add(
                            scoped_label
                        )
                    per_query_chunks.setdefault(q_idx, []).extend(result)

                total_q_count = len(queries) + len(facet_pool)
                results_per_query = []
                for q_idx in range(total_q_count):
                    if q_idx in per_query_chunks:
                        raw = per_query_chunks[q_idx]
                        seen: set[str] = set()
                        deduped: list[_RetrievedChunk] = []
                        for chunk in raw:
                            if chunk.content_hash not in seen:
                                seen.add(chunk.content_hash)
                                deduped.append(chunk)
                                if q_idx >= facet_q_offset:
                                    facet_chunk_hashes.add(chunk.content_hash)
                        results_per_query.append(deduped)
                    elif q_idx in per_query_errors:
                        results_per_query.append(per_query_errors[q_idx])
                    else:
                        results_per_query.append([])
            else:
                pool_a_tasks_simple = [
                    _execute_single_query(
                        client,
                        api_path,
                        q,
                        top_k,
                        recency_weight,
                        search_scope,
                        source_prefixes,
                        query_embedding=_query_embeddings.get(q_idx),
                    )
                    for q_idx, q in enumerate(queries)
                ]

                if _inline_facets:
                    # Concurrent: pool A + IDF/phrase computation → pool B
                    (
                        pool_a_raw,
                        (computed_facets, facet_pool, pool_b_raw),
                    ) = await asyncio.gather(
                        asyncio.gather(
                            *pool_a_tasks_simple,
                            return_exceptions=True,
                        ),
                        _compute_and_dispatch_pool_b(
                            client,
                            api_path,
                            context.source_text,
                            facet_top_k,
                            recency_weight,
                            search_scope,
                            source_prefixes,
                            max_phrases=_idf_max_phrases,
                            max_idf_terms=_idf_max_terms,
                            max_discriminative=_idf_max_disc,
                        ),
                    )
                    results_per_query = list(pool_a_raw) + pool_b_raw
                    if facet_pool:
                        logger.info(
                            "Step '%s': pool B — %d facet sparse queries "
                            "(top_k=%d, inline=True): %s",
                            step.id,
                            len(facet_pool),
                            facet_top_k,
                            [label for label, _ in facet_pool],
                        )
                else:
                    if facet_pool:
                        logger.info(
                            "Step '%s': pool B — %d facet sparse queries "
                            "(top_k=%d): %s",
                            step.id,
                            len(facet_pool),
                            facet_top_k,
                            [label for label, _ in facet_pool],
                        )
                        for _, or_query in facet_pool:
                            pool_a_tasks_simple.append(
                                _execute_single_query(
                                    client,
                                    api_path,
                                    or_query,
                                    facet_top_k,
                                    recency_weight,
                                    search_scope,
                                    source_prefixes,
                                    sparse_only=True,
                                )
                            )
                    results_per_query = await asyncio.gather(
                        *pool_a_tasks_simple, return_exceptions=True
                    )

                pool_b_start = len(queries)
                for i, result in enumerate(
                    results_per_query[pool_b_start:], pool_b_start
                ):
                    if not isinstance(result, BaseException):
                        for chunk in result:
                            facet_chunk_hashes.add(chunk.content_hash)

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
                    error=f"all {len(queries) + len(facet_pool)} queries failed",
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

        # --- Synthetic scope demotion (post-RRF, pre-cap) ---
        # Chunks found ONLY via the synthetic (broad) scope get their RRF
        # score reduced so leaf-scope hits are preferred in ranking.
        synthetic_demoted_count = 0
        if synthetic_scope_name and synthetic_demotion < 1.0 and chunk_all_scopes:
            for chunk in merged:
                hit_scopes = chunk_all_scopes.get(chunk.content_hash)
                if not hit_scopes:
                    continue
                if hit_scopes == {synthetic_scope_name}:
                    old = merged_scores.get(chunk.content_hash, 0.0)
                    merged_scores[chunk.content_hash] = old * synthetic_demotion
                    synthetic_demoted_count += 1
            if synthetic_demoted_count > 0:
                merged.sort(
                    key=lambda c: merged_scores.get(c.content_hash, 0.0),
                    reverse=True,
                )
                logger.info(
                    "Step '%s': synthetic scope demotion (×%.2f) applied to %d/%d chunks",
                    step.id,
                    synthetic_demotion,
                    synthetic_demoted_count,
                    len(merged),
                )

        # Optional per-scope cap applied after RRF ranking, before junk filtering.
        if scope_chunk_caps:
            capped: list[_RetrievedChunk] = []
            seen_per_scope: dict[str, int] = {}
            for chunk in merged:
                chunk_scope = chunk_scope_by_hash.get(chunk.content_hash, "").strip()
                if not chunk_scope or chunk_scope not in scope_chunk_caps:
                    capped.append(chunk)
                    continue
                curr = seen_per_scope.get(chunk_scope, 0)
                if curr >= scope_chunk_caps[chunk_scope]:
                    merged_scores.pop(chunk.content_hash, None)
                    continue
                seen_per_scope[chunk_scope] = curr + 1
                capped.append(chunk)
            merged = capped

        # --- Post-RRF junk / noise filter (metadata + line-shape heuristic) ---
        if _noise_filter_disable_effective(effective):
            junk_threshold = 0.0
        else:
            junk_threshold = _noise_filter_threshold_effective(
                effective, step_id=step.id
            )
        pre_junk = len(merged)
        clean: list[_RetrievedChunk] = []
        if junk_threshold <= 0:
            clean = merged
        else:
            for chunk in merged:
                md = chunk.metadata
                meta_noise = (
                    isinstance(md, dict) and chunk_metadata_is_noise(md)
                ) or chunk_is_noise(chunk.content, threshold=junk_threshold)
                if meta_noise:
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

        # --- Source diversity cap (post-junk-filter, pre-neighbor-expansion) ---
        source_diversity_max = int(effective.get("source_diversity_max", 0))
        source_diversity_dropped = 0
        if source_diversity_max > 0:
            chunks_before_diversity = len(merged)
            source_counts: dict[str, int] = {}
            diverse: list[_RetrievedChunk] = []
            for chunk in merged:
                src = chunk.source
                count = source_counts.get(src, 0)
                if count >= source_diversity_max:
                    merged_scores.pop(chunk.content_hash, None)
                    source_diversity_dropped += 1
                    continue
                source_counts[src] = count + 1
                diverse.append(chunk)
            if source_diversity_dropped > 0:
                logger.info(
                    "Step '%s': source diversity cap (%d/source) dropped %d/%d chunks",
                    step.id,
                    source_diversity_max,
                    source_diversity_dropped,
                    len(merged),
                )
                self._publish_bus_event(
                    context,
                    RagRetrievalSourceDiversityLimited(
                        pipeline_id=context.pipeline.id,
                        execution_id=context.execution_id,
                        step_name=step.name,
                        per_source_limit=source_diversity_max,
                        chunks_dropped=source_diversity_dropped,
                        chunks_before=chunks_before_diversity,
                        chunks_after=len(diverse),
                    ),
                )
            merged = diverse

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
                RagNeighborExpansionApplied(
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
        chunks_before_coverage_selection = len(merged)

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

        # Pool B score boost with lateral source habituation:
        # - Highest-scoring Pool B chunk per source: full boost (signal detected)
        # - Subsequent chunks from same source: inhibited by 1/boost (lateral inhibition)
        # ∀ source: chunks ranked by current score; first *= boost, rest *= 1/boost
        # Tradeoff: a second chunk from the same source may be richer than the first,
        # but the system accepts that risk in exchange for making room for other sources.
        # Callers that need depth over breadth (e.g. single authoritative document)
        # should set facet_pool_score_boost=1.0 or use retrieval_path="general".
        facet_pool_score_boost = float(effective.get("facet_pool_score_boost", 1.5))
        if facet_chunk_hashes and facet_pool_score_boost != 1.0:
            # Group pool B chunks by source, sorted descending by current score.
            facet_by_source: dict[str, list[str]] = {}
            for c in merged:
                if c.content_hash in facet_chunk_hashes:
                    facet_by_source.setdefault(c.source, []).append(c.content_hash)
            for source_hashes in facet_by_source.values():
                source_hashes.sort(
                    key=lambda h: merged_scores.get(h, 0.0), reverse=True
                )
                for n, h in enumerate(source_hashes):
                    if h not in merged_scores:
                        continue
                    # n=0 → boost; n≥1 → inhibit (sensory habituation)
                    multiplier = (
                        facet_pool_score_boost
                        if n == 0
                        else (1.0 / facet_pool_score_boost)
                    )
                    merged_scores[h] = merged_scores[h] * multiplier
            merged.sort(
                key=lambda c: merged_scores.get(c.content_hash, 0.0), reverse=True
            )

        # Global source habituation: apply the same diminishing-returns inhibition
        # to ALL chunks grouped by source — pool A included.
        # After the first (highest-scored) chunk from any source is seen, the system
        # has received that source's signal; subsequent chunks carry less new information.
        # ∀ source: nth chunk (n≥1, 0-indexed) → score *= (1/factor)^n
        source_habituation_factor = float(
            effective.get("source_habituation_factor", facet_pool_score_boost)
        )
        if source_habituation_factor != 1.0:
            source_seen: dict[str, int] = {}  # source → count of chunks seen so far
            for c in merged:  # already sorted descending by score
                n = source_seen.get(c.source, 0)
                if n > 0 and c.content_hash in merged_scores:
                    merged_scores[c.content_hash] /= source_habituation_factor**n
                source_seen[c.source] = n + 1
            merged.sort(
                key=lambda c: merged_scores.get(c.content_hash, 0.0), reverse=True
            )

        # Pool B swap: for sources with pool B hits, evict redundant pool A chunks.
        # Graduated mode (threshold > 0): retain up to max_retain pool A chunks
        # whose cosine distance from all pool B hits exceeds the threshold —
        # they cover genuinely different content.
        # Binary mode (threshold = 0, default): evict all pool A chunks from
        # sources with any pool B hit (original behavior).
        facet_pool_swap_enabled = bool(effective.get("facet_pool_swap_enabled", True))
        if facet_pool_swap_enabled and facet_chunk_hashes:
            swap_distance_threshold = float(
                effective.get("facet_pool_swap_distance_threshold", 0.0)
            )
            swap_max_retain = int(effective.get("facet_pool_swap_max_retain", 2))

            if swap_distance_threshold > 0.0:
                async with make_async_client(
                    base_url, timeout=rag_timeout
                ) as swap_client:
                    swap_result = await _graduated_pool_b_swap(
                        swap_client,
                        api_path.replace("/search", "/embed_batch"),
                        merged,
                        facet_chunk_hashes,
                        swap_distance_threshold,
                        swap_max_retain,
                        scope=(
                            search_scope
                            if isinstance(search_scope, str | list)
                            else None
                        ),
                    )
                merged = swap_result.chunks
                if swap_result.evicted or swap_result.retained:
                    logger.info(
                        "Step '%s': graduated pool B swap — evicted %d, "
                        "retained %d pool A chunks "
                        "(threshold=%.2f, max_retain=%d)",
                        step.id,
                        swap_result.evicted,
                        swap_result.retained,
                        swap_distance_threshold,
                        swap_max_retain,
                    )
            else:
                facet_sources: set[str] = {
                    c.source for c in merged if c.content_hash in facet_chunk_hashes
                }
                swapped_out: set[str] = {
                    c.content_hash
                    for c in merged
                    if c.content_hash not in facet_chunk_hashes
                    and c.source in facet_sources
                }
                if swapped_out:
                    merged = [c for c in merged if c.content_hash not in swapped_out]
                    logger.info(
                        "Step '%s': pool B swap — evicted %d pool A chunks "
                        "from %d sources",
                        step.id,
                        len(swapped_out),
                        len(facet_sources),
                    )

        if coverage_enabled:
            self._publish_bus_event(
                context,
                RagCoverageSelectionApplied(
                    pipeline_id=context.pipeline.id,
                    execution_id=context.execution_id,
                    step_name=step.name,
                    enabled=True,
                    applied=boost_result.applied,
                    chunks_before=chunks_before_coverage_selection,
                    chunks_after=len(merged),
                ),
            )

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
                "metadata": (
                    {
                        **c.metadata,
                        "retrieval_scope": chunk_scope_by_hash[c.content_hash],
                    }
                    if c.content_hash in chunk_scope_by_hash
                    else c.metadata
                ),
                "content_hash": c.content_hash,
                "score": boost_result.scores.get(c.content_hash, 0.0),
            }
            for c in merged
        ]
        context_text = (
            format_context(chunk_dicts) if chunk_dicts else _NO_RESULTS_SENTINEL
        )
        if not context_text.strip():
            context_text = _NO_RESULTS_SENTINEL

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
        predicted_scope_for_event_raw = rewrite_data.get("scopes", ["unknown"])
        if (
            isinstance(predicted_scope_for_event_raw, list)
            and predicted_scope_for_event_raw
            and isinstance(predicted_scope_for_event_raw[0], str)
        ):
            predicted_scope_for_event = predicted_scope_for_event_raw[0]
        else:
            predicted_scope_for_event = str(predicted_scope_for_event_raw)
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
                "queries_executed": len(queries) + len(facet_pool),
                "queries_succeeded": len(successful),
                "raw_chunks_total": total_raw,
                "scope": scope,
                "rewritten_queries": queries,
                "facet_pool_queries": [q for _, q in facet_pool] or None,
                "facets": computed_facets or None,
                "chunks": chunk_dicts,
                "effective_params": {
                    "top_k_per_query": top_k,
                    "max_chunks": max_chunks,
                    "rrf_k": rrf_k,
                    "recency_weight": recency_weight,
                    "retrieval_path": retrieval_path,
                    "scope_key": scope_key,
                    "scope_defaults_applied": params.scope_profile or None,
                    "scope_confidence_threshold": confidence_threshold,
                    "rag_scope_chunk_caps": scope_chunk_caps,
                    "source_diversity_max": source_diversity_max or None,
                    "source_diversity_dropped": source_diversity_dropped,
                    "synthetic_scope": synthetic_scope_name or None,
                    "synthetic_demotion": synthetic_demotion
                    if synthetic_scope_name
                    else None,
                    "synthetic_demoted_count": synthetic_demoted_count,
                    "consumer_model": consumer_model or None,
                    "consumer_tier": consumer_tier,
                    "profile_applied": bool(
                        consumer_model and params.exact_model_profile
                    ),
                    "tier_applied": bool(consumer_tier and params.tier_profile),
                    "facet_pool_swap_distance_threshold": float(
                        effective.get("facet_pool_swap_distance_threshold", 0.0)
                    ),
                    "facet_pool_swap_max_retain": int(
                        effective.get("facet_pool_swap_max_retain", 2)
                    ),
                },
            },
        )

    def _scope_rejection_output(
        self,
        context: PipelineContext,
        step: StepConfig,
        reason: str,
        scope: str | list[str],
        details: str,
    ) -> StepOutput:
        """Emit scope rejection event and return fail-closed no-results output.

        Used when the determined scope is invalid, unknown, or has insufficient
        confidence, leading to zero retrieval results.

        Args:
            context: The pipeline context.
            step: The current step configuration.
            reason: Short code for the rejection (e.g. invalid_scope_override).
            scope (str | list[str]): The rejected scope. Can be a single scope
                string or a list of scope strings.
            details: Descriptive message about the rejection.

        Returns:
            StepOutput with raw=no-results sentinel and scope_rejected=True.
        """
        logger.info(
            "Step '%s': scope rejected — reason=%s, scope=%s, details=%s",
            step.id,
            reason,
            scope,
            details,
        )
        self._publish_bus_event(
            context,
            RagScopeRejected(
                pipeline_id=context.pipeline.id,
                execution_id=context.execution_id,
                step_name=step.name,
                reason=reason,
                scope=scope,
                details=details,
            ),
        )
        return StepOutput(
            raw=_NO_RESULTS_SENTINEL,
            json={
                "chunks_found": 0,
                "queries_executed": 0,
                "chunks": [],
                "scope_rejected": True,
                "scope_rejection_reason": reason,
            },
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        """Validate step config: endpoint and scope_result handler_input required."""
        errors: list[str] = []
        if not step.get_domain_field("endpoint"):
            errors.append(f"Step '{step.id}' missing required 'endpoint' field")
        if not step.handler_inputs or "scope_result" not in step.handler_inputs:
            errors.append(f"Step '{step.id}' missing 'scope_result' in handler_inputs")
        return errors
