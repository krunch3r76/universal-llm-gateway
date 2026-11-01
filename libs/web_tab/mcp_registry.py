"""MCP ``web_tab_read`` op ↔ pipeline id mapping (category ``web_tab``)."""

from __future__ import annotations

import time
from typing import Any

WEB_TAB_READ_OP_TO_PIPELINE: dict[str, str] = {
    "status": "web-tab-status",
    "probe": "web-tab-probe",
}

WEB_TAB_READ_PIPELINE_CATEGORY = "web_tab"

_INSTALLED_CACHE: tuple[float, frozenset[str]] | None = None
_CACHE_TTL_S = 60.0


def advertised_read_ops(*, installed_pipeline_ids: frozenset[str] | None = None) -> frozenset[str]:
    """Ops exposed when backing pipelines are installed."""
    ids = installed_pipeline_ids
    if ids is None:
        ids = query_installed_web_tab_pipeline_ids()
    return frozenset(
        op for op, pid in WEB_TAB_READ_OP_TO_PIPELINE.items() if pid in ids
    )


def query_installed_web_tab_pipeline_ids() -> frozenset[str]:
    """Query Stargate capabilities for category ``web_tab`` (cached ≤60s)."""
    global _INSTALLED_CACHE
    now = time.monotonic()
    if _INSTALLED_CACHE is not None and now - _INSTALLED_CACHE[0] < _CACHE_TTL_S:
        return _INSTALLED_CACHE[1]
    ids = _fetch_installed_from_stargate()
    _INSTALLED_CACHE = (now, ids)
    return ids


def _fetch_installed_from_stargate() -> frozenset[str]:
    try:
        from tools.pipeline import _pipeline_list
    except ImportError:
        return frozenset(WEB_TAB_READ_OP_TO_PIPELINE.values())
    resp: dict[str, Any] = _pipeline_list(WEB_TAB_READ_PIPELINE_CATEGORY)
    if resp.get("error"):
        return frozenset()
    members = resp.get("members") or {}
    installed: set[str] = set()
    for pid, meta in members.items():
        if not isinstance(meta, dict):
            continue
        if meta.get("category") == WEB_TAB_READ_PIPELINE_CATEGORY:
            installed.add(str(pid))
    return frozenset(installed)
