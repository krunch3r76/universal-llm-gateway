"""todo_close v1 handlers — register the single-step closure handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .close import TodoCloseApplyHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "todo_close",
        "todo_close_apply_v1",
        TodoCloseApplyHandler,
    )
