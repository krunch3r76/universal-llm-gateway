from __future__ import annotations

from typing import TYPE_CHECKING

from .apply import GuardedApplyHandler
from .collect import CollectHandler
from .cursor import CursorLoadHandler, CursorSaveHandler
from .enrich import EnrichHandler
from .report import ReportHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "dream_state",
        "dream_state_cursor_v1",
        CursorLoadHandler,
    )
    router.register_domain_handler_class(
        "dream_state",
        "dream_state_collect_v1",
        CollectHandler,
    )
    router.register_domain_handler_class(
        "dream_state",
        "dream_state_enrich_v1",
        EnrichHandler,
    )
    router.register_domain_handler_class(
        "dream_state",
        "dream_state_apply_v1",
        GuardedApplyHandler,
    )
    router.register_domain_handler_class(
        "dream_state",
        "dream_state_cursor_save_v1",
        CursorSaveHandler,
    )
    router.register_domain_handler_class(
        "dream_state",
        "dream_state_report_v1",
        ReportHandler,
    )
