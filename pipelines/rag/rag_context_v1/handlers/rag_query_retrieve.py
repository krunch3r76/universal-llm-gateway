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
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

import httpx
import yaml
from systems.pipeline.core.events.step import RagRetrievalParamsResolved
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_PROFILES_PATH = (
    Path(__file__).resolve().parent.parent.parent / "retrieval-profiles.yaml"
)
_profiles_cache: dict[str, Any] | None = None


def _load_retrieval_profiles() -> dict[str, Any]:
    """Load retrieval profiles from YAML (cached after first load).

    Returns top-level dict with ``profiles`` and ``scope_defaults`` keys.
    Returns empty dict if file is missing.
    """
    global _profiles_cache  # noqa: PLW0603
    if _profiles_cache is not None:
        return _profiles_cache

    if not _PROFILES_PATH.exists():
        logger.info("No retrieval profiles at %s", _PROFILES_PATH)
        result: dict[str, Any] = {}
        _profiles_cache = result
        return result

    with _PROFILES_PATH.open() as f:
        result = yaml.safe_load(f) or {}

    _profiles_cache = result
    logger.info(
        "Loaded retrieval profiles: %d model(s), %d scope default(s)",
        len(result.get("profiles", {})),
        len(result.get("scope_defaults", {})),
    )
    return result


_NO_RETRIEVAL_SENTINEL = "No retrieval needed — answering from model knowledge."
_NO_RESULTS_SENTINEL = "No relevant documents found in the knowledge base."


@dataclass(slots=True)
class _RetrievedChunk:
    """Single chunk from a RAG search result."""

    content: str
    source: str
    indexed_at: str
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        self.content_hash = hashlib.md5(
            self.content.encode(), usedforsecurity=False
        ).hexdigest()


def _rrf_merge(
    results_per_query: list[list[_RetrievedChunk]],
    k: int = 60,
    max_chunks: int = 20,
) -> list[_RetrievedChunk]:
    """Reciprocal rank fusion across multiple query result sets.

    RRF score: score(chunk) = Σ 1/(k + rank_i + 1), summed across queries
    where rank_i is the 0-based position in query i's results.

    Cosine distances from different queries are incomparable —
    RRF uses rank order only.
    """
    scores: dict[str, float] = {}
    chunks: dict[str, _RetrievedChunk] = {}

    for results in results_per_query:
        for rank, chunk in enumerate(results):
            cid = chunk.content_hash
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            if cid not in chunks:
                chunks[cid] = chunk

    sorted_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [chunks[cid] for cid in sorted_ids[:max_chunks]]


def _format_context(chunks: list[_RetrievedChunk]) -> str:
    """Format merged chunks for prompt injection."""
    if not chunks:
        return _NO_RESULTS_SENTINEL

    sections = [
        f"[Source: {c.source} | Last changed: {c.indexed_at}]\n{c.content}"
        for c in chunks
    ]
    return "\n\n---\n\n".join(sections)


async def _execute_single_query(
    client: httpx.AsyncClient,
    endpoint: str,
    query: str,
    top_k: int,
    recency_weight: float,
    scope: str | None,
    source_prefixes: list[str] | None,
) -> list[_RetrievedChunk]:
    """Execute one RAG search and parse results into chunks."""
    body: dict[str, Any] = {
        "query": query,
        "top_k": top_k,
        "recency_weight": recency_weight,
    }
    if source_prefixes:
        body["source_prefixes"] = source_prefixes
    elif scope:
        body["scope"] = scope

    response = await client.post(endpoint, json=body)
    response.raise_for_status()
    data = response.json()

    raw_chunks: list[str] = data.get("chunks", [])
    metadata: list[dict[str, Any]] = data.get("metadata", [])

    return [
        _RetrievedChunk(
            content=chunk,
            source=str(meta.get("source", "unknown")),
            indexed_at=str(meta.get("indexed_at", "unknown")),
        )
        for chunk, meta in zip(raw_chunks, metadata, strict=True)
    ]


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
        consumer_model (optional — triggers profile lookup from retrieval-profiles.yaml).

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
        endpoint: str = step.get_domain_field("endpoint", "")
        if not endpoint:
            raise ValueError(f"Step '{step.id}': missing 'endpoint' domain field")

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
                json={"chunks_found": 0, "queries_executed": 0},
            )

        queries: list[str] = rewrite_data.get("rewritten_queries", [])
        if not queries:
            queries = [context.source_text]
            logger.info(
                "Step '%s': no rewritten queries, falling back to source text",
                step.id,
            )

        # --- Three-tier tunable resolution ---
        # Tier 1: YAML defaults from pipeline spec
        yaml_defaults = context.pipeline.options.to_context_dict()
        runtime = context.runtime_options

        # Tier 2: retrieval profile for consumer model (if provided by caller)
        profiles_data = _load_retrieval_profiles()
        consumer_model: str = runtime.get("consumer_model", "")
        profile = profiles_data.get("profiles", {}).get(consumer_model, {})

        # Class-based fallback: match consumer_model against model_classes patterns
        matched_class_name: str | None = None
        if consumer_model and not profile:
            for class_name, class_config in profiles_data.get(
                "model_classes", {}
            ).items():
                pattern = class_config.get("match", "")
                if pattern and pattern in consumer_model:
                    profile = {k: v for k, v in class_config.items() if k != "match"}
                    matched_class_name = class_name
                    logger.info(
                        "Step '%s': no exact profile for '%s', "
                        "using model_class '%s' (match='%s')",
                        step.id,
                        consumer_model,
                        class_name,
                        pattern,
                    )
                    break

        if consumer_model and profile:
            logger.info(
                "Step '%s': applying retrieval profile for consumer '%s'",
                step.id,
                consumer_model,
            )

        # Merge: yaml_defaults < profile < runtime (runtime always wins)
        effective = {**yaml_defaults, **profile, **runtime}

        top_k = int(effective.get("rag_top_k_per_query", 10))
        max_chunks = int(effective.get("rag_max_chunks", 20))
        rrf_k = int(effective.get("rag_rrf_k", 35))
        recency_weight = float(effective.get("rag_recency_weight", 0.2))
        confidence_threshold = float(effective.get("scope_confidence_threshold", 0.7))

        # --- Scope resolution ---
        explicit_prefixes: list[str] | None = effective.get("rag_source_prefixes")
        if explicit_prefixes:
            source_prefixes = explicit_prefixes
            scope = "custom"
            search_scope = None  # use raw prefixes
            retrieval_mode = "source_prefixes"
        else:
            scope_override_val: str = effective.get("scope_override", "")
            if scope_override_val:
                scope = scope_override_val
            else:
                predicted_scope = rewrite_data.get("scope", "both")
                scope_confidence = float(rewrite_data.get("scope_confidence", 1.0))
                scope = (
                    predicted_scope
                    if scope_confidence >= confidence_threshold
                    else "both"
                )
                if scope != predicted_scope:
                    logger.info(
                        "Step '%s': scope_confidence=%.2f < threshold=%.2f, "
                        "overriding scope '%s' → 'both'",
                        step.id,
                        scope_confidence,
                        confidence_threshold,
                        predicted_scope,
                    )
            source_prefixes = None  # let RAG service resolve
            search_scope = scope  # pass scope label to /search
            retrieval_mode = "scope"

        # Tier 3: scope-conditional recency (unless caller explicitly overrode it)
        if "rag_recency_weight" not in runtime:
            scope_recency = (
                profiles_data.get("scope_defaults", {})
                .get(scope, {})
                .get("rag_recency_weight")
            )
            if scope_recency is not None:
                recency_weight = float(scope_recency)

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
                profile_class=matched_class_name,
                max_chunks=max_chunks,
                top_k_per_query=top_k,
                rrf_k=rrf_k,
                scope=scope,
                retrieval_mode=retrieval_mode,
                uses_explicit_prefixes=bool(source_prefixes),
            ),
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = [
                _execute_single_query(
                    client,
                    endpoint,
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
            logger.warning("Step '%s': all queries failed", step.id)
            return StepOutput(
                raw=_NO_RESULTS_SENTINEL,
                json={"chunks_found": 0, "queries_executed": len(queries)},
            )

        merged = _rrf_merge(successful, k=rrf_k, max_chunks=max_chunks)
        context_text = _format_context(merged)

        total_raw = sum(len(r) for r in successful)
        logger.info(
            "Step '%s': retrieved %d raw chunks → %d after RRF merge",
            step.id,
            total_raw,
            len(merged),
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
                "effective_params": {
                    "top_k_per_query": top_k,
                    "max_chunks": max_chunks,
                    "rrf_k": rrf_k,
                    "recency_weight": recency_weight,
                    "scope_confidence_threshold": confidence_threshold,
                    "consumer_model": consumer_model or None,
                    "profile_applied": bool(consumer_model and profile),
                },
            },
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        if not step.get_domain_field("endpoint"):
            errors.append(f"Step '{step.id}' missing required 'endpoint' field")
        if not step.handler_inputs or "rewrite_result" not in step.handler_inputs:
            errors.append(
                f"Step '{step.id}' missing 'rewrite_result' in handler_inputs"
            )
        return errors
