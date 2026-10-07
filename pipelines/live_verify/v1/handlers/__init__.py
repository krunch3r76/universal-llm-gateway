"""live_verify v1 handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .verdict import LiveVerifyVerdictHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "live_verify",
        "live_verify_verdict_v1",
        LiveVerifyVerdictHandler,
    )
