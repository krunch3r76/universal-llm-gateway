from __future__ import annotations

from typing import TYPE_CHECKING

from .validate import ValidateHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "journal_extract", "journal_extract_validate_v1", ValidateHandler,
    )
