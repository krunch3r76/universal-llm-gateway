"""orion_chat_wake v1 — pointer paste into chatgpt.com."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .wake import OrionChatWakeHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "orion_chat_wake",
        "orion_chat_wake_v1",
        OrionChatWakeHandler,
    )
