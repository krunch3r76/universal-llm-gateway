"""web_tab v1 handlers — eyes slice (status, probe)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .probe import WebTabProbeHandler
from .status import WebTabStatusHandler
from .write import WebTabWriteHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class("web_tab", "web_tab_status_v1", WebTabStatusHandler)
    router.register_domain_handler_class("web_tab", "web_tab_probe_v1", WebTabProbeHandler)
    router.register_domain_handler_class("web_tab", "web_tab_write_v1", WebTabWriteHandler)
