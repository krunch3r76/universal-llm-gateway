"""continuity_consolidate v1 handlers — ingest (graph read + watermark gate) and apply (validated writes)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .apply import ContinuityConsolidateApplyHandler
from .ingest import ContinuityConsolidateIngestHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "continuity_consolidate",
        "continuity_consolidate_ingest_v1",
        ContinuityConsolidateIngestHandler,
    )
    router.register_domain_handler_class(
        "continuity_consolidate",
        "continuity_consolidate_apply_v1",
        ContinuityConsolidateApplyHandler,
    )
