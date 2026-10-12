"""display_chat_wake v1 — one step, the sidebar-gated paste."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .wake import DisplayChatWakeHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register the single display-chat-wake step on the domain router.

    The pipeline loader calls this at process start. The only step type
    is ``display_chat_wake_v1``. A title mismatch stays a completed run.
    """
    router.register_domain_handler_class(
        "display_chat_wake",
        "display_chat_wake_v1",
        DisplayChatWakeHandler,
    )
