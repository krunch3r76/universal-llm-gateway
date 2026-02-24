"""
RAG v3.5 handler registration.

v3.5 owns its rag_search_v1 copy — independent of v3 evolution.
shell_v1, rag_source_v1, coalesce_v1, and assess_loop_v1 are generic
handlers registered by pipelines/tools/handlers/ — no registration needed here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .rag_search import RagSearchHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register all rag/project_journal_v3.5 handlers."""
    router.register_domain_handler_class("rag", "rag_search_v1", RagSearchHandler)
