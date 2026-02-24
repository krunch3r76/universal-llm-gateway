"""
Pipeline tools handler registration.

Registers generic (domain-agnostic) tool handlers available to all pipeline types.
Loaded by user_handlers.py as shared handlers for the "tools" domain directory,
but registered via register_generic_handler_class for tier-3 (universal) resolution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .rag_source import RagSourceHandler
from .shell import ShellHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register all tool handlers as generic (available to any pipeline domain)."""
    router.register_generic_handler_class("shell_v1", ShellHandler)
    router.register_generic_handler_class("rag_source_v1", RagSourceHandler)
