"""MCP life_dispatch tool — thin relay to Stargate POST /api/v1/life/dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client

if TYPE_CHECKING:
    from fastmcp import FastMCP

_TIMEOUT = 60.0


def _relay_post(body: dict[str, Any]) -> dict[str, Any]:
    with make_sync_client(DEFAULT_STARGATE_URL, timeout=_TIMEOUT) as client:
        try:
            response = client.post("/api/v1/life/dispatch", json=body)
        except httpx.RequestError as exc:
            return {"error": f"life-dispatch relay failed: {exc}", "status_code": None}
    try:
        payload = response.json()
    except ValueError:
        return {
            "error": f"invalid JSON from life/dispatch: {response.text[:200]}",
            "status_code": response.status_code,
        }
    if response.status_code >= 400:
        payload.setdefault("status_code", response.status_code)
    return payload


def register_life_dispatch_tools(mcp: FastMCP) -> None:
    @mcp.tool(title="Life Dispatch")
    def life_dispatch(
        prompt: str = "",
        thread: str = "",
        model: str = "cdp/opus-5",
        skills: list[str] | None = None,
    ) -> dict[str, Any]:
        """Life CDP dispatch — opens the configured Life Cowork project compose.

        Supply ``prompt`` (inline text or ``cortex://`` sidecar ref) **or**
        ``thread`` (agent_bus id). For ``thread``, the latest turn must be
        addressed ``to=life`` or ``to=dispatch`` and must not be ``from=life``
        — otherwise admit returns a teaching 422. ``model`` defaults to
        ``cdp/opus-5``. Project UUID is server-pinned — never in this schema.
        """
        body: dict[str, Any] = {"model": model}
        if prompt.strip():
            body["prompt"] = prompt.strip()
        elif thread.strip():
            body["thread"] = thread.strip()
        else:
            return {
                "error": "exactly one of prompt or thread is required",
                "status_code": 422,
            }
        if skills:
            body["skills"] = skills
        return _relay_post(body)
