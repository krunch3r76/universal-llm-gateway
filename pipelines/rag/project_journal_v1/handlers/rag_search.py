"""
RAG search step handler.

Calls the RAG service and returns pre-filtered, formatted context text.
The LLM never sees raw distances — server-side max_distance handles filtering.

Invariants:
- ∀ execute(): returns StepOutput.raw = formatted context text (never empty string)
- ∀ empty result: returns "No relevant documents found" sentinel (LLM handles gracefully)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

import httpx
from systems.pipeline.core.handlers.protocol import AbstractStepHandler, StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class RagSearchHandler(AbstractStepHandler):
    """
    Execute a RAG search against the local RAG service.

    Domain fields (from pipeline YAML step config):
        endpoint: str       — RAG service URL (e.g. http://localhost:8100/search)
        top_k: int          — number of results to retrieve (default 5)
        recency_weight: float — recency decay weight 0.0–1.0 (default 0.0)
        distance_threshold: float — max_distance filter (default 1.0)
    """

    step_type: str = "rag_search_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        query = context.source_text
        top_k: int = step.get_domain_field("top_k", 5)
        recency_weight: float = step.get_domain_field("recency_weight", 0.0)
        distance_threshold: float = step.get_domain_field("distance_threshold", 1.0)
        endpoint: str | None = step.get_domain_field("endpoint")

        if not endpoint:
            raise ValueError(f"Step '{step.id}': missing required 'endpoint' field")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                endpoint,
                json={
                    "query": query,
                    "top_k": top_k,
                    "recency_weight": recency_weight,
                    "max_distance": distance_threshold,
                },
            )
            response.raise_for_status()

        data = response.json()
        chunks: list[str] = data.get("chunks", [])
        metadata: list[dict] = data.get("metadata", [])

        if not chunks:
            context_text = "No relevant documents found in the knowledge base."
        else:
            sections = []
            for chunk, meta in zip(chunks, metadata, strict=True):
                source = meta.get("source", "unknown")
                indexed_at = meta.get("indexed_at", "unknown")
                sections.append(f"[Source: {source} | Indexed: {indexed_at}]\n{chunk}")
            context_text = "\n\n---\n\n".join(sections)

        logger.debug(
            f"RAG search '{step.id}': query={query[:60]!r}, chunks_found={len(chunks)}"
        )

        return StepOutput(
            raw=context_text,
            json={"chunks_found": len(chunks)},
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors = []
        if not step.get_domain_field("endpoint"):
            errors.append(f"Step '{step.id}' missing required 'endpoint' field")
        return errors
