"""MCP ``web_tab_read`` op ↔ pipeline id mapping (category ``web_tab``).

The mapping is pure. Live Stargate queries and their cache live on the MCP
adapter (``tools/web_tab_read``), not in this library.
"""

from __future__ import annotations

WEB_TAB_READ_OP_TO_PIPELINE: dict[str, str] = {
    "status": "web-tab-status",
    "probe": "web-tab-probe",
}

WEB_TAB_READ_PIPELINE_CATEGORY = "web_tab"


def advertised_read_ops(*, installed_pipeline_ids: frozenset[str]) -> frozenset[str]:
    """Return read ops whose pipeline ids are in *installed_pipeline_ids*.

    Does not query Stargate. An empty set means those pipelines are absent
    from a successful listing, not that the registry was unreachable.
    """
    return frozenset(
        op
        for op, pid in WEB_TAB_READ_OP_TO_PIPELINE.items()
        if pid in installed_pipeline_ids
    )
