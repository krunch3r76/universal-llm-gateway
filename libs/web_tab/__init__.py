"""Web-tab substrate — read (status/probe) and write (open/bind/paste) actuation planes.

``CONSUMERS`` names processes that load this package: Stargate via dynamic
``pipelines/web_tab`` handlers, and MCP via ``tools/web_tab_read`` /
``tools/web_tab_write``.
"""

from web_tab.mcp_registry import (
    WEB_TAB_READ_OP_TO_PIPELINE,
    WEB_TAB_WRITE_OP_TO_PIPELINE,
    advertised_read_ops,
    advertised_write_ops,
)
from web_tab.probe import probe_target
from web_tab.status_report import build_status_report

CONSUMERS: tuple[str, ...] = ("mcp", "stargate")

__all__ = [
    "CONSUMERS",
    "WEB_TAB_READ_OP_TO_PIPELINE",
    "WEB_TAB_WRITE_OP_TO_PIPELINE",
    "advertised_read_ops",
    "advertised_write_ops",
    "build_status_report",
    "probe_target",
]
