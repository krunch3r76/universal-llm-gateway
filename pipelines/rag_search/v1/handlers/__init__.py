"""rag_search v1 handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .fetch_scope_catalog_retry import FetchScopeCatalogRetryHandler
from .rerank import RerankHandler
from .retrieval_metadata import RetrievalMetadataHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register rag_search v1 step types."""
    router.register_domain_handler_class(
        "rag_search", "retrieval_metadata_step", RetrievalMetadataHandler
    )
    router.register_domain_handler_class(
        "rag_search", "rerank_v1", RerankHandler
    )
    router.register_domain_handler_class(
        "rag_search",
        "fetch_scope_catalog_retry_v1",
        FetchScopeCatalogRetryHandler,
    )
