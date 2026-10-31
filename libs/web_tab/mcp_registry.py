"""MCP ``web_tab_read`` op ↔ pipeline id mapping (category ``web_tab``)."""

from __future__ import annotations

WEB_TAB_READ_OP_TO_PIPELINE: dict[str, str] = {
    "status": "web-tab-status",
    "probe": "web-tab-probe",
}

WEB_TAB_READ_PIPELINE_CATEGORY = "web_tab"


def advertised_read_ops(*, installed_pipeline_ids: frozenset[str] | None = None) -> frozenset[str]:
    """Ops exposed when backing pipelines are installed."""
    ids = installed_pipeline_ids
    if ids is None:
        ids = frozenset(WEB_TAB_READ_OP_TO_PIPELINE.values())
    return frozenset(
        op
        for op, pid in WEB_TAB_READ_OP_TO_PIPELINE.items()
        if pid in ids
    )
