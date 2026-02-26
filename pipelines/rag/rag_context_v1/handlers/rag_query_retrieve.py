"""
Multi-query RAG retrieval with reciprocal rank fusion (RRF).

Reads structured output from an upstream query-rewriting step, executes
parallel RAG searches, and merges results via RRF into a single ranked
context block.

Invariants:
- ∀ execute(): returns StepOutput.raw = formatted context text (never empty string)
- ∀ needs_retrieval=false: returns sentinel (generation step handles gracefully)
- ∀ RRF merge: deduplicates by chunk content hash, scores by rank only
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, override

import httpx
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

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


def _resolve_source_prefixes(
    step: StepConfig,
    scope: str,
) -> list[str] | None:
    """Map scope label to filesystem source prefixes for RAG filtering."""
    research = step.get_domain_field("research_prefix", "")
    project = step.get_domain_field("project_prefix", "")

    match scope:
        case "research" if research:
            return [research]
        case "project" if project:
            return [project]
        case "both":
            prefixes = [p for p in (research, project) if p]
            return prefixes or None
        case _:
            return None


async def _execute_single_query(
    client: httpx.AsyncClient,
    endpoint: str,
    query: str,
    top_k: int,
    recency_weight: float,
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

    Domain fields (from pipeline YAML step config):
        endpoint: str               — RAG service URL
        top_k_per_query: int        — chunks per sub-query (default 10)
        max_chunks: int             — total chunks after RRF (default 20)
        recency_weight: float       — recency bias 0.0–1.0 (default 0.2)
        rrf_k: int                  — RRF constant (default 60)
        research_prefix: str        — source prefix for research scope
        project_prefix: str         — source prefix for project scope

    handler_inputs:
        rewrite_result              — bound to upstream step's .json output
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

        scope = rewrite_data.get("scope", "both")
        source_prefixes = _resolve_source_prefixes(step, scope)
        top_k = step.get_domain_field("top_k_per_query", 10)
        max_chunks = step.get_domain_field("max_chunks", 20)
        rrf_k = step.get_domain_field("rrf_k", 60)
        recency_weight: float = step.get_domain_field("recency_weight", 0.2)

        logger.info(
            "Step '%s': executing %d queries (scope=%s, top_k=%d, rrf_k=%d)",
            step.id,
            len(queries),
            scope,
            top_k,
            rrf_k,
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = [
                _execute_single_query(
                    client, endpoint, q, top_k, recency_weight, source_prefixes
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
