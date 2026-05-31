"""plan_seed v1 handlers — register the single-step seed handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .apply import PlanSeedApplyHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "plan_seed",
        "plan_seed_apply_v1",
        PlanSeedApplyHandler,
    )
