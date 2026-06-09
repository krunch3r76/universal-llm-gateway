"""implement_closeout v1 handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .apply import ImplementCloseoutApplyHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "implement_closeout",
        "implement_closeout_apply_v1",
        ImplementCloseoutApplyHandler,
    )
