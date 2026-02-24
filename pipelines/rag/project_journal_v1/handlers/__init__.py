"""
RAG v1 handler registration.

Registers the rag_search_v1 step handler for the rag domain, v1 variant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .rag_search import RagSearchHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register all rag/project_journal_v1 handlers."""
    router.register_domain_handler_class("rag", "rag_search_v1", RagSearchHandler)
