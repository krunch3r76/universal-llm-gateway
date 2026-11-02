"""Call-time liaison GIW endpoint URLs (env override or ``giw_api_url`` default path)."""

from __future__ import annotations

import os

from giw_endpoint import giw_api_url

_ACTIVE_WORK_PATH = "/api/v1/git/active-work"
_USAGE_LIVE_PATH = "/api/v1/cursor/dispatch-usage-live"
_HEALTH_PATH = "/health"


def liaison_giw_active_work_url() -> str:
    """URL for GIW ``/api/v1/git/active-work`` (roster and quiet_reason probes)."""
    override = os.environ.get("LIAISON_GIW_ACTIVE_WORK")
    if override is not None and override.strip():
        return override.strip()
    return giw_api_url(_ACTIVE_WORK_PATH)


def liaison_giw_usage_live_url() -> str:
    """URL for GIW dispatch-usage-live (digest budget)."""
    override = os.environ.get("LIAISON_GIW_USAGE_LIVE")
    if override is not None and override.strip():
        return override.strip()
    return giw_api_url(_USAGE_LIVE_PATH)


def liaison_giw_health_url() -> str:
    """URL for GIW ``/health`` (liaison digest fleet block)."""
    override = os.environ.get("LIAISON_GIW_HEALTH")
    if override is not None and override.strip():
        return override.strip()
    return giw_api_url(_HEALTH_PATH)
