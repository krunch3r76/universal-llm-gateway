"""Web-tab observation substrate — read-only status/probe for connector vs actuation planes.

Binding writers and actuation side effects live in later slices; this package
implements eyes-first observation only.

``CONSUMERS`` names processes that load this package: Stargate via dynamic
``pipelines/web_tab`` handlers, and MCP via ``tools/web_tab_read``.
"""

from web_tab.mcp_registry import WEB_TAB_READ_OP_TO_PIPELINE, advertised_read_ops
from web_tab.probe import probe_target
from web_tab.status_report import build_status_report

CONSUMERS: tuple[str, ...] = ("mcp", "stargate")

__all__ = [
    "CONSUMERS",
    "WEB_TAB_READ_OP_TO_PIPELINE",
    "advertised_read_ops",
    "build_status_report",
    "probe_target",
]
