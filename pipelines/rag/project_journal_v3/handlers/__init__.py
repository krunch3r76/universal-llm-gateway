"""
RAG v3 handler registration.

v3 owns its handler set. rag_search_v1 is v3's own copy — search semantics
are unchanged from v2; the copy ensures v3 is independent of v2 evolution.
shell_v1, rag_source_v1, and coalesce_v1 are generic handlers registered by
pipelines/tools/handlers/ — no registration needed here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .rag_search import RagSearchHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register all rag/project_journal_v3 handlers."""
    router.register_domain_handler_class("rag", "rag_search_v1", RagSearchHandler)
