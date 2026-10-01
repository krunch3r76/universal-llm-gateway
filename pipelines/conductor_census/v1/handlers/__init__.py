"""conductor_census v1 — register the read-only census step."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .census import ConductorCensusHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "conductor_census",
        "conductor_census_v1",
        ConductorCensusHandler,
    )
