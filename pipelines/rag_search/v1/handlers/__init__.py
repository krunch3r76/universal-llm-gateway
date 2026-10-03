"""rag_search v1 handler registration (copied from rag/rag_context_v1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .direct_scope import DirectScopeHandler
from .rag_emit_context import RagEmitContextHandler
from .rag_query_retrieve import RagMultiRetrieveHandler
from .rag_rerank_assemble import RagRerankAssembleHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register rag_search v1 step types."""
    router.register_domain_handler_class(
        "rag_search", "rag_direct_scope_v1", DirectScopeHandler
    )
    router.register_domain_handler_class(
        "rag_search", "rag_multi_retrieve_v1", RagMultiRetrieveHandler
    )
    router.register_domain_handler_class(
        "rag_search", "rag_rerank_assemble_v1", RagRerankAssembleHandler
    )
    router.register_domain_handler_class(
        "rag_search", "rag_emit_context_v1", RagEmitContextHandler
    )
