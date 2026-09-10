"""Thin Stargate relays for continuity MCP ops (tape-read, checkpoint)."""

from __future__ import annotations

import os
from typing import Any

import httpx
from transport_utils import make_sync_client
from universal_logging import get_logger

from tools._restart_probe import annotate_unreachable_error
from tools.pipeline import STARGATE_URL

logger = get_logger(__name__)

_TAPE_READ_TIMEOUT = 60.0
_CHECKPOINT_TIMEOUT = 15.0


def _continuity_tape_read(
    *,
    thread: str,
    scope: str = "full",
    transcript_id: str | None = None,
    prior_cells: int | None = None,
    include_extras: bool = False,
    tools: str = "none",
    budget_bytes: int | None = None,
    harvest: bool = False,
) -> dict[str, Any]:
    """POST Stargate ``/api/v1/continuity/tape-read`` (Door 1 sync relay)."""
    from continuity_tape.events import mcp_continuity_tape_read_requested

    mcp_continuity_tape_read_requested(thread=thread, scope=scope, door="sync")
    body: dict[str, Any] = {
        "thread": thread,
        "scope": scope,
        "include_extras": include_extras,
        "tools": tools,
        "harvest": harvest,
    }
    if transcript_id:
        body["transcript_id"] = transcript_id
    if prior_cells is not None:
        body["prior_cells"] = prior_cells
    if budget_bytes is not None:
        body["budget_bytes"] = budget_bytes
    stargate_url = os.environ.get("STARGATE_URL", STARGATE_URL)
    try:
        with make_sync_client(stargate_url, timeout=_TAPE_READ_TIMEOUT) as client:
            resp = client.post("/api/v1/continuity/tape-read", json=body)
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except ValueError:
                payload = {
                    "error": {
                        "code": f"http_{resp.status_code}",
                        "message": resp.text[:500],
                    }
                }
            if isinstance(payload, dict):
                payload.setdefault("status_code", resp.status_code)
            return payload
        return resp.json()
    except httpx.ConnectError as exc:
        return annotate_unreachable_error(
            code="stargate_unreachable",
            message=f"Stargate not reachable: {exc}",
            service="stargate",
        )
    except httpx.HTTPError as exc:
        return {"error": {"code": "http_error", "message": str(exc)}}


def _continuity_checkpoint(
    *,
    thread: str,
    surface: str,
    from_agent: str | None = None,
    transcript_id: str | None = None,
    jsonl_path: str | None = None,
    chat_url: str | None = None,
    residue: str | None = None,
    pre_consolidate: bool = True,
    tools: str = "none",
) -> dict[str, Any]:
    """POST Stargate ``/api/v1/continuity/checkpoint`` (async pipeline relay)."""
    from continuity_tape.events import mcp_continuity_checkpoint_requested

    mcp_continuity_checkpoint_requested(surface=surface, thread=thread)
    body: dict[str, Any] = {
        "thread": thread,
        "surface": surface,
        "from_agent": from_agent or "cursor",
        "pre_consolidate": pre_consolidate,
        "tools": tools,
    }
    if transcript_id is not None:
        body["transcript_id"] = transcript_id
    if jsonl_path is not None:
        body["jsonl_path"] = jsonl_path
    if chat_url is not None:
        body["chat_url"] = chat_url
    if residue is not None:
        body["residue"] = residue
    stargate_url = os.environ.get("STARGATE_URL", STARGATE_URL)
    try:
        with make_sync_client(stargate_url, timeout=_CHECKPOINT_TIMEOUT) as client:
            resp = client.post("/api/v1/continuity/checkpoint", json=body)
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except ValueError:
                payload = {
                    "error": {
                        "code": f"http_{resp.status_code}",
                        "message": resp.text[:500],
                    }
                }
            if isinstance(payload, dict):
                payload.setdefault("status_code", resp.status_code)
            return payload
        return resp.json()
    except httpx.ConnectError as exc:
        return annotate_unreachable_error(
            code="stargate_unreachable",
            message=f"Stargate not reachable: {exc}",
            service="stargate",
        )
    except httpx.HTTPError as exc:
        return {"error": {"code": "http_error", "message": str(exc)}}


def _continuity_resume(
    *,
    thread: str,
    transcript_id: str | None = None,
    pool: str | None = None,
    source: str = "mcp",
) -> dict[str, Any]:
    """POST agent-bus ``/threads/{thread}/resume-fence`` (Door 1 bundle pour)."""
    from tools.agent_bus._shared import relay

    body: dict[str, Any] = {"source": source}
    if transcript_id is not None:
        body["transcript_id"] = transcript_id
    if pool is not None:
        body["pool"] = pool
    return relay("agent-bus", "POST", f"/threads/{thread}/resume-fence", body=body)


def _continuity_resume_release(*, fence_id: str) -> dict[str, Any]:
    """POST agent-bus ``/resume-fences/{fence_id}/release``."""
    from tools.agent_bus._shared import relay

    return relay("agent-bus", "POST", f"/resume-fences/{fence_id}/release")


__all__ = [
    "_continuity_checkpoint",
    "_continuity_resume",
    "_continuity_resume_release",
    "_continuity_tape_read",
]
