"""MCP tool for querying model load/busy/loading status from Stargate.

Connectivity: MCP container → Stargate host on port 9999 via
host.docker.internal (extra_hosts in compose) or STARGATE_URL env override.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://host.docker.internal:9999")
_TIMEOUT = 15.0


def _get_headers() -> dict[str, str]:
    """Auth headers for Stargate API calls."""
    token = os.environ.get("STARGATE_API_KEY", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def register_model_status_tools(mcp: FastMCP) -> None:
    """Register the model_status tool on the MCP server."""

    @mcp.tool()
    def model_status(
        model_id: str | None = None,
        status_filter: str | None = None,
    ) -> dict[str, Any]:
        """Query model load/busy/loading status across all Stargate nodes.

        Without ``model_id``: returns all models with per-model placement.
        With ``model_id``: returns detail for one model; if the ID is not in the
        catalog, returns ``{"error": "Model not found: ..."}`` (HTTP 404 from
        Stargate is mapped to that dict — this tool does not raise).

        Use this to check whether a model is loaded before sending inference,
        or to debug routing by seeing which models are loaded where.

        Prefer ``manage`` for starting services; use this for read-only
        placement and load-state inspection.

        Args:
            model_id: Optional specific model to query. Omit for all models.
            status_filter: Optional filter: loaded, busy, loading, available.
                           Only applies to the all-models listing.

        Returns:
            On success: JSON from Stargate. For a single model, entries include
            ``id``, ``status``, ``activated``, ``available``, ``summary`` (with
            ``loaded_on``, ``busy_on``, ``loading_on``), and per-node detail.
            For all models, a listing with ``models``, counts, etc. On failure
            or unreachable Stargate: ``{"error": ...}`` (and optionally
            ``detail`` for HTTP errors).
        """
        if model_id:
            url = f"{_STARGATE_URL}/api/v1/model-status/{model_id}"
        else:
            url = f"{_STARGATE_URL}/api/v1/model-status"

        params: dict[str, str] = {}
        if status_filter and not model_id:
            params["status"] = status_filter

        try:
            resp = httpx.get(
                url,
                headers=_get_headers(),
                params=params,
                timeout=_TIMEOUT,
            )
            if resp.status_code == 404:
                return {"error": f"Model not found: {model_id}"}
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            return {"error": "Cannot reach Stargate — is it running?"}
        except httpx.TimeoutException:
            return {"error": "Stargate request timed out"}
        except httpx.HTTPStatusError as exc:
            return {
                "error": f"HTTP {exc.response.status_code}",
                "detail": exc.response.text,
            }
        except Exception as exc:
            logger.error("model_status failed: %s", exc, exc_info=True)
            return {"error": str(exc)}
