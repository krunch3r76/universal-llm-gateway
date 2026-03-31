"""MCP tool for querying model load/busy/loading status from Stargate.

Connectivity: MCP container → Stargate host on port 9999 via
Stargate master via STARGATE_URL env (default: http://io:9999).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")
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
        """Query model load/busy/loading status across Stargate nodes.

        Full docs: fs(op="md_read", sandbox="project", path="docs/tool-reference.md", section="model_status")
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
