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
    """Register model_status and list_models tools on the MCP server."""

    @mcp.tool(title="List Models")
    def list_models(
        filter: str | None = None,
        type: str | None = None,
    ) -> dict[str, Any]:
        """List all models available through the gateway.

        Calls Stargate ``GET /v1/models`` — the same catalog a human client
        or any OpenAI-compatible tool would see.  Use this to discover model
        IDs before calling ``llm_generate`` or ``frontier_generate``.

        Args:
            filter: Optional provider prefix to narrow results.
                Accepted values: ``anthropic``, ``xai``, ``openai``,
                ``openrouter``, ``local`` (no-slash IDs).
                Omit to return all models.
            type: Optional type filter. Accepted values: ``model`` (cloud/local
                inference models), ``pipeline`` (activated pipeline contexts).
                Omit to return all entries.

        Returns:
            ``{"models": [{"id": str, "type": str, "owned_by": str}, ...],
            "total": int, "filter": str | None, "type": str | None}``
        """
        url = f"{_STARGATE_URL}/v1/models"
        try:
            resp = httpx.get(url, headers=_get_headers(), timeout=_TIMEOUT)
            resp.raise_for_status()
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
            logger.error("list_models failed: %s", exc, exc_info=True)
            return {"error": str(exc)}

        raw = resp.json()
        models: list[dict[str, Any]] = raw.get("data", [])

        if filter:
            if filter == "local":
                models = [m for m in models if "/" not in m.get("id", "")]
            else:
                prefix = filter.rstrip("/") + "/"
                models = [m for m in models if m.get("id", "").startswith(prefix)]

        if type:
            models = [m for m in models if m.get("type") == type]

        return {
            "models": [
                {
                    "id": m.get("id", ""),
                    "type": m.get("type", "model"),
                    "owned_by": m.get("owned_by", ""),
                }
                for m in models
            ],
            "total": len(models),
            "filter": filter,
            "type": type,
        }

    @mcp.tool(title="Model Status")
    def model_status(
        model_id: str | None = None,
        status_filter: str | None = None,
    ) -> dict[str, Any]:
        """Query model load/busy/loading status across Stargate nodes.

        Args:
          model_id      (str|None) — specific model to query; omit for all models
          status_filter (str|None) — filter by status: loaded, busy, loading, available (all-models only)

        Without model_id: returns GPU worker load/busy/loading state across nodes.
        With model_id: returns detail for one model ({"error": "Model not found: ..."} if missing).

        Use ``list_models()`` to discover available model IDs — this tool reports
        runtime load state, not the model catalog.
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
