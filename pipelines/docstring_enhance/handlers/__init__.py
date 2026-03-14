"""
docstring-enhance pipeline handler registration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .collect_inventory import CollectInventoryHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register docstring-enhance domain handlers."""
    router.register_domain_handler_class(
        "docstring_enhance",
        "docstring_enhance_collect_inventory",
        CollectInventoryHandler,
    )
