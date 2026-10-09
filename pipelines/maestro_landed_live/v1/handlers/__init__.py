"""maestro_landed_live v1 handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .dry_run import MaestroLandedLiveDryRunHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "maestro_landed_live",
        "maestro_landed_live_dry_run_v1",
        MaestroLandedLiveDryRunHandler,
    )
