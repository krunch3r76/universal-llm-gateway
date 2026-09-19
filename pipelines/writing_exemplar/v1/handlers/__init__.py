"""writing_exemplar v1 handlers — register-aware rag-context retrieve."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .retrieve import WritingExemplarRetrieveHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "writing_exemplar",
        "writing_exemplar_retrieve_v1",
        WritingExemplarRetrieveHandler,
    )
