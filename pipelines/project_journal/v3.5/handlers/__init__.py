"""
project_journal v3.5 variant handler registration.

pipeline_call_v1 is registered at the shared domain level
(project_journal/handlers/) — no variant-specific handlers needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """No variant-specific handlers for v3.5."""
    pass
