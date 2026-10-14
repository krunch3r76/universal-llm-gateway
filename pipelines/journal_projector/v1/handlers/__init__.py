"""journal_projector v1 handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .append_handler import JournalProjectorAppendHandler
from .parse_lesson import JournalProjectorParseHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    pairs = [
        ("journal_projector_parse_v1", JournalProjectorParseHandler),
        ("journal_projector_append_v1", JournalProjectorAppendHandler),
    ]
    for step_type, cls in pairs:
        router.register_domain_handler_class("journal_projector", step_type, cls)


__all__ = ["register_handlers"]
