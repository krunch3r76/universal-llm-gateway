"""assertion_enrichment v1 handlers — prospective + events writeback steps."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .apply import (
    AssertionEnrichmentEventsHandler,
    AssertionEnrichmentProspectiveHandler,
)

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "assertion_enrichment",
        "assertion_enrichment_prospective_v1",
        AssertionEnrichmentProspectiveHandler,
    )
    router.register_domain_handler_class(
        "assertion_enrichment",
        "assertion_enrichment_events_v1",
        AssertionEnrichmentEventsHandler,
    )
