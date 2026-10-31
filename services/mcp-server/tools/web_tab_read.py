"""MCP web_tab_read — thin relay to web_tab category pipelines (eyes: status, probe)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from mcp_events import record
from web_tab.mcp_registry import WEB_TAB_READ_OP_TO_PIPELINE, advertised_read_ops

from tools.pipeline import _pipeline_run

if TYPE_CHECKING:
    from fastmcp import FastMCP


def _resolve_pipeline(op: str, *, installed: frozenset[str] | None = None) -> str | None:
    allowed = advertised_read_ops(installed_pipeline_ids=installed)
    if op not in allowed:
        return None
    return WEB_TAB_READ_OP_TO_PIPELINE[op]


def register_web_tab_read_tool(mcp: FastMCP) -> None:
    """Register read-only web_tab MCP dispatcher."""

    @mcp.tool(title="Web tab (read)")
    def web_tab_read(
        op: Literal["status", "probe"],
        house_id: str | None = None,
        agent: str | None = None,
        target_id: str | None = None,
        cdp_url: str | None = None,
        surface: str | None = None,
        connector_alive: bool | None = None,
        last_attested_turn: int | None = None,
        attestation_source: str | None = None,
    ) -> dict[str, Any]:
        """Read-only web-tab status and probe — relays to installed ``web_tab`` pipelines.

        No navigation, bind, paste, or repair. Op enum matches pipelines in category
        ``web_tab`` that are loaded on Stargate (status → ``web-tab-status``,
        probe → ``web-tab-probe``).
        """
        pipeline_id = _resolve_pipeline(op)
        if pipeline_id is None:
            return {
                "error": {
                    "code": "op_not_loaded",
                    "message": f"web_tab_read op {op!r} is not installed",
                    "advertised_ops": sorted(advertised_read_ops()),
                }
            }
        options: dict[str, Any] = {
            k: v
            for k, v in {
                "house_id": house_id,
                "agent": agent,
                "target_id": target_id,
                "cdp_url": cdp_url,
                "surface": surface,
                "connector_alive": connector_alive,
                "last_attested_turn": last_attested_turn,
                "attestation_source": attestation_source,
            }.items()
            if v is not None
        }
        record("mcp.web_tab_read.called", op=op, pipeline_id=pipeline_id)
        return _pipeline_run(
            pipeline_id,
            messages=[{"role": "user", "content": op}],
            options=options or None,
            timeout=None,
        )
