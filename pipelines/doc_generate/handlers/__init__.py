"""
doc-generate pipeline handler registration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .enforce_grounding import EnforceGroundingHandler
from .extract_docstrings import ExtractDocstringsHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register doc-generate domain handlers."""
    router.register_domain_handler_class(
        "doc_generate",
        "doc_generate_extract_docstrings",
        ExtractDocstringsHandler,
    )
    router.register_domain_handler_class(
        "doc_generate",
        "doc_generate_enforce_grounding",
        EnforceGroundingHandler,
    )
