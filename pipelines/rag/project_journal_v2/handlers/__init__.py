"""
RAG v2 handler registration.

v2 owns its handler set. rag_search_v1 is v2's own copy — search semantics
are unchanged from v1; the copy ensures v2 is independent of v1 evolution.
shell_v1 and rag_source_v1 are generic handlers registered by
pipelines/tools/handlers/ — no registration needed here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .rag_search import RagSearchHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register all rag/project_journal_v2 handlers."""
    router.register_domain_handler_class("rag", "rag_search_v1", RagSearchHandler)
