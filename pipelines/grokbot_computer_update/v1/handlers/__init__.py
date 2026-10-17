"""grokbot_computer_update v1 — one zero-token control step."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .update import GrokbotComputerUpdateHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "grokbot_computer_update",
        "grokbot_computer_update_v1",
        GrokbotComputerUpdateHandler,
    )
