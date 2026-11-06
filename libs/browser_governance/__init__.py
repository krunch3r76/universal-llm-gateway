"""Shared browser governance contract (C0).

``CONSUMERS`` names manage slugs that may import this shared contract for
browser census, admission preflight, and supervision (R1/S1 slices).
"""

from browser_governance.contract import (
    DEFAULT_WARN_DISK_FREE_GIB,
    DEFAULT_WARN_MEM_AVAILABLE_MIB,
    DEFAULT_WARN_SWAP_FREE_MIB,
    EXCLUDED_MESSAGE_ANCESTOR_SELECTOR,
    SUPERVISION_BANNER_SCAN_JS,
    admission_preflight_mode,
    build_supervision_envelope,
    census_enabled,
    classify_ui_banner_text,
    grok_keepalive_displays,
    sanitize_ui_banner_text,
    supervision_gate_mode,
    unscanned_supervision,
    warn_disk_free_gib,
    warn_mem_available_mib,
    warn_swap_free_mib,
)
from browser_governance.events import (
    browser_admission_warn,
    browser_resource_census,
    browser_supervision_error,
    browser_supervision_visibility,
    emit_browser_event,
)

CONSUMERS: tuple[str, ...] = ("agent_bus", "cdp_ask", "mcp", "stargate")

__all__ = [
    "CONSUMERS",
    "DEFAULT_WARN_DISK_FREE_GIB",
    "DEFAULT_WARN_MEM_AVAILABLE_MIB",
    "DEFAULT_WARN_SWAP_FREE_MIB",
    "EXCLUDED_MESSAGE_ANCESTOR_SELECTOR",
    "SUPERVISION_BANNER_SCAN_JS",
    "admission_preflight_mode",
    "browser_admission_warn",
    "browser_resource_census",
    "browser_supervision_error",
    "browser_supervision_visibility",
    "build_supervision_envelope",
    "census_enabled",
    "classify_ui_banner_text",
    "emit_browser_event",
    "grok_keepalive_displays",
    "sanitize_ui_banner_text",
    "supervision_gate_mode",
    "unscanned_supervision",
    "warn_disk_free_gib",
    "warn_mem_available_mib",
    "warn_swap_free_mib",
]
