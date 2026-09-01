"""Lane↔branch association MCP dispatchers."""

from __future__ import annotations

from typing import Any

from mcp_events import record

from ._shared import _structured_relay_error, relay


def _branch_associate_impl(*, thread_id: str, branch_name: str) -> dict[str, Any]:
    """Append one association via POST /threads/{thread_id}/branch-associate."""
    payload = {"branch_name": branch_name}
    result = relay(
        "agent-bus",
        "POST",
        f"/threads/{thread_id}/branch-associate",
        body=payload,
    )
    if "error" in result:
        structured = _structured_relay_error(result, op="branch_associate")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    record(
        "mcp.agentbus.branch_associate",
        thread_id=thread_id,
        branch_name=branch_name,
        association_id=result.get("id"),
    )
    return result


def _branch_current_impl(*, thread_id: str) -> dict[str, Any]:
    """Read derived current branch via GET /threads/{thread_id}/branch-current."""
    result = relay(
        "agent-bus",
        "GET",
        f"/threads/{thread_id}/branch-current",
    )
    if "error" in result:
        structured = _structured_relay_error(result, op="branch_current")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    record(
        "mcp.agentbus.branch_current",
        thread_id=thread_id,
        state=result.get("state"),
    )
    return result


def _branch_associate_dispatch(
    *,
    thread_id: str,
    branch_name: str,
    id: int | None = None,  # noqa: A002
    seq: int | None = None,
) -> dict[str, Any]:
    if id is not None or seq is not None:
        tokens = [name for name, val in (("id", id), ("seq", seq)) if val is not None]
        return {
            "error": (
                "agent-bus error: client-supplied ordering tokens not allowed: "
                + ", ".join(tokens)
            )
        }
    return _branch_associate_impl(thread_id=thread_id, branch_name=branch_name)


def _branch_current_dispatch(*, thread_id: str) -> dict[str, Any]:
    return _branch_current_impl(thread_id=thread_id)
