"""MCP trigger tool — thin httpx relay to git-integration-worker triggers API."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

import httpx
from mcp_events import record

if TYPE_CHECKING:
    from fastmcp import FastMCP

_DEFAULT_WORKER_URL = "http://127.0.0.1:8091"
_API_PREFIX = "/api/v1/triggers"


def _worker_base_url() -> str:
    explicit = os.environ.get("GIT_INTEGRATION_WORKER_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    stargate = os.environ.get("STARGATE_URL", "").strip()
    if stargate:
        return stargate.rstrip("/")
    return _DEFAULT_WORKER_URL


def _bearer_headers() -> dict[str, str]:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _relay(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    url = f"{_worker_base_url()}{_API_PREFIX}{path}"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.request(
                method,
                url,
                json=json_body,
                params=params,
                headers=_bearer_headers(),
            )
            if resp.content:
                body = resp.json()
            else:
                body = {"ok": True}
            if resp.status_code >= 400:
                record(
                    "mcp.trigger.relay.failed",
                    path=path,
                    status=resp.status_code,
                )
                if isinstance(body, dict):
                    body.setdefault("status_code", resp.status_code)
                return body
            return body
    except httpx.RequestError as exc:
        record("mcp.trigger.relay.failed", path=path, kind="unreachable")
        return {"error": f"trigger relay unreachable: {exc}"}


def register_trigger_tool(mcp: FastMCP) -> None:
    """Register trigger schedule relay on *mcp*."""

    @mcp.tool(title="Trigger Schedule")
    def trigger(
        op: Literal["schedule", "list", "get", "cancel"],
        trigger_id: str | None = None,
        created_by: str = "life-seat",
        fire_at: str | None = None,
        delay_s: float | None = None,
        prompt_uri: str | None = None,
        prompt_text: str | None = None,
        purpose: str = "operator-proxy",
        model: str = "opus-5",
        arc: str | None = None,
        so_what: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Schedule, list, get, or cancel ULG-hosted operator-proxy triggers.

        Thin relay to ``GIT_INTEGRATION_WORKER_URL`` (or Stargate proxy)
        ``/api/v1/triggers/*``. Bearer auth uses ``AGENT_BUS_TOKEN``.

        Ops:
          schedule — POST new trigger (requires fire_at or delay_s + prompt)
          list — GET all triggers
          get — GET one trigger by id (includes fire record)
          cancel — DELETE / cancel a scheduled trigger
        """
        if op == "schedule":
            body = {
                k: v
                for k, v in {
                    "created_by": created_by,
                    "fire_at": fire_at,
                    "delay_s": delay_s,
                    "prompt_uri": prompt_uri,
                    "prompt_text": prompt_text,
                    "purpose": purpose,
                    "model": model,
                    "arc": arc,
                    "so_what": so_what,
                }.items()
                if v is not None and v != ""
            }
            result = _relay("POST", "", json_body=body)
            record("mcp.trigger.schedule", ok="error" not in result)
            return result

        if op == "list":
            return _relay("GET", "", params={"limit": limit})

        if not trigger_id:
            return {"error": f"{op} requires trigger_id"}

        if op == "get":
            return _relay("GET", f"/{trigger_id}")

        return _relay("DELETE", f"/{trigger_id}")
