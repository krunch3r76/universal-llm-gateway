"""cursor_paste_resolve v1 handlers — densify, compose, then launch."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .compose import CursorPasteComposeHandler
from .densify import CursorPasteDensifyHandler
from .launch import CursorPasteLaunchHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "cursor_paste_resolve",
        "cursor_paste_resolve_densify_v1",
        CursorPasteDensifyHandler,
    )
    router.register_domain_handler_class(
        "cursor_paste_resolve",
        "cursor_paste_resolve_compose_v1",
        CursorPasteComposeHandler,
    )
    router.register_domain_handler_class(
        "cursor_paste_resolve",
        "cursor_paste_resolve_launch_v1",
        CursorPasteLaunchHandler,
    )
