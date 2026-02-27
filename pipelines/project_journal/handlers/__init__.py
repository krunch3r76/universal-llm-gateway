"""
project_journal shared handler registration.

pipeline_call_v1 is provided by core builtin; no domain-specific handlers here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register all shared project_journal handlers."""
    # pipeline_call_v1 is registered as core builtin; no domain registration needed
    pass
