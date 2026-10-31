"""Web-tab observation substrate — read-only status/probe for connector vs actuation planes.

Binding writers and actuation side effects live in later slices; this package
implements eyes-first observation only.
"""

from web_tab.mcp_registry import WEB_TAB_READ_OP_TO_PIPELINE, advertised_read_ops
from web_tab.probe import probe_target
from web_tab.status_report import build_status_report

__all__ = [
    "WEB_TAB_READ_OP_TO_PIPELINE",
    "advertised_read_ops",
    "build_status_report",
    "probe_target",
]
