"""predicate_extract v1 handlers — register the single-step extractor."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .extract import PredicateExtractApplyHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "predicate_extract",
        "predicate_extract_apply_v1",
        PredicateExtractApplyHandler,
    )
