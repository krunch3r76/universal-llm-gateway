"""MCP tool for a compact live system topology snapshot.

Combines node VRAM state (from /api/v1/gateways/status/full) with currently
loaded/busy models and their slot counts (from /api/v1/model-status).

Use this tool for orientation — understanding what nodes exist, what's loaded
where, and how many inference slots each model has.  For a single model's
detailed hardware profile, use model_status(model_id="…") instead.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")
_TIMEOUT = 15.0


def _get_headers() -> dict[str, str]:
    token = os.environ.get("STARGATE_API_KEY", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _get(path: str) -> dict[str, Any] | None:
    try:
        resp = httpx.get(
            f"{STARGATE_URL}{path}",
            headers=_get_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("topology fetch %s failed: %s", path, exc)
        return None


def register_topology_tools(mcp: FastMCP) -> None:
    """Register the topology tool on the MCP server."""

    @mcp.tool(title="System Topology")
    def topology(loaded_only: bool = True) -> dict[str, Any]:
        """Compact live snapshot of nodes and model placements.

        Returns two sections:
        - ``nodes``: each gateway node with VRAM state and connection status.
          ``vram_free_mb`` is current free VRAM — not a placement ceiling, since
          Stargate evicts idle models automatically to make room.
        - ``models``: models currently loaded or busy across nodes, with
          per-node ``parallel_slots`` and ``effective_context_per_slot``.

        Args:
            loaded_only: When True (default), ``models`` lists only models
                that are currently loaded or busy.  Set to False to include
                all catalog-available models (large list).

        Use ``model_status(model_id="…")`` for full hardware detail on a
        specific model.  Use ``list_models()`` to browse the full catalog.
        """
        errors: list[str] = []

        # --- node inventory ---
        gw_data = _get("/api/v1/gateways/status/full")
        nodes: dict[str, Any] = {}
        if gw_data is None:
            errors.append("gateways/status/full unavailable")
        else:
            for gw_url, gw in gw_data.get("gateways", {}).items():
                node_id = gw.get("node_id") or gw_url
                nodes[node_id] = {
                    "connected": gw.get("is_connected", False),
                    "enabled": gw.get("enabled", True),
                    "vram_free_mb": gw.get("available_vram_mb"),
                    "vram_total_mb": gw.get("total_vram_mb"),
                }

        # --- model placements ---
        status_path = "/api/v1/model-status"
        if loaded_only:
            status_path += "?status=loaded"

        ms_data = _get(status_path)
        models: list[dict[str, Any]] = []
        if ms_data is None:
            errors.append("model-status unavailable")
        else:
            for entry in ms_data.get("models", []):
                mid = entry.get("id", "")
                status = entry.get("status", "available")
                if loaded_only and status not in ("loaded", "busy"):
                    continue

                placed_nodes = sorted(
                    entry.get("summary", {}).get("loaded_on", [])
                    + entry.get("summary", {}).get("busy_on", [])
                    + entry.get("summary", {}).get("loading_on", [])
                )

                # pull slot info from model-status/{id} hardware block if present
                hardware = entry.get("hardware", {})
                slot_info: dict[str, Any] = {}
                for node_id, hw in hardware.items():
                    if "parallel_slots" in hw:
                        slot_info[node_id] = {
                            "parallel_slots": hw["parallel_slots"],
                            "effective_context_per_slot": hw.get(
                                "effective_context_per_slot"
                            ),
                        }

                model_entry: dict[str, Any] = {
                    "id": mid,
                    "status": status,
                    "nodes": placed_nodes,
                }
                if slot_info:
                    model_entry["slots"] = slot_info

                models.append(model_entry)

        result: dict[str, Any] = {"nodes": nodes, "models": models}
        if errors:
            result["errors"] = errors
        return result
