"""
project_journal shared handler registration.

pipeline_call_v1 — calls any pipeline via Stargate chat completions.
Shared across all project_journal variants.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .pipeline_call import PipelineCallHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register all shared project_journal handlers."""
    router.register_domain_handler_class(
        "project_journal", "pipeline_call_v1", PipelineCallHandler
    )
