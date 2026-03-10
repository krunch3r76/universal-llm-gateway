"""
RAG rag_context_v1 handler registration.

Registers rag_multi_retrieve_v1 (retrieval + RRF merge),
rag_rerank_assemble_v1 (LLM reranking + context formatting), and
filter_corpus_hints_v1 (query-conditioned hint filtering).
Built-in ``generate`` handles the analyze_rewrite step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .filter_hints import FilterCorpusHintsHandler
from .rag_query_retrieve import RagMultiRetrieveHandler
from .rag_rerank_assemble import RagRerankAssembleHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register all rag/rag_context_v1 handlers."""
    router.register_domain_handler_class(
        "rag", "rag_multi_retrieve_v1", RagMultiRetrieveHandler
    )
    router.register_domain_handler_class(
        "rag", "rag_rerank_assemble_v1", RagRerankAssembleHandler
    )
    router.register_domain_handler_class(
        "rag", "filter_corpus_hints_v1", FilterCorpusHintsHandler
    )
