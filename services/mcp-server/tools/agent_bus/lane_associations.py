"""Lane parentage association MCP dispatchers (V7).

Thin relay over store ``/threads/{id}/lane-bind`` and ``/lane-current``.
Not a contract token. Distinct from ``branch_associate`` (git).
"""

from __future__ import annotations

from typing import Any

from mcp_events import record

from ._shared import _structured_relay_error, relay


def _normalize_thread(value: str | int | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _lane_bind_impl(
    *,
    thread_id: str,
    parent_thread_id: str,
    lane_role: str,
    bound_by: str | None,
    evidence: str | None,
) -> dict[str, Any]:
    """Append one association via POST /threads/{thread_id}/lane-bind."""
    payload: dict[str, Any] = {
        "parent_thread_id": parent_thread_id,
        "lane_role": lane_role,
    }
    if bound_by is not None:
        payload["bound_by"] = bound_by
    if evidence is not None:
        payload["evidence"] = evidence
    result = relay(
        "agent-bus",
        "POST",
        f"/threads/{thread_id}/lane-bind",
        body=payload,
    )
    if "error" in result:
        structured = _structured_relay_error(result, op="lane_bind")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    record(
        "mcp.agentbus.lane_bind",
        thread_id=thread_id,
        parent_thread_id=parent_thread_id,
        lane_role=lane_role,
        association_id=result.get("id"),
    )
    return result


def _lane_current_impl(*, thread_id: str) -> dict[str, Any]:
    """Read derived current parentage via GET /threads/{thread_id}/lane-current."""
    result = relay(
        "agent-bus",
        "GET",
        f"/threads/{thread_id}/lane-current",
    )
    if "error" in result:
        structured = _structured_relay_error(result, op="lane_current")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    record(
        "mcp.agentbus.lane_current",
        thread_id=thread_id,
        state=result.get("state"),
    )
    return result


def _lane_bind_dispatch(
    *,
    thread: str | int | None = None,
    thread_id: str | int | None = None,
    parent_thread: str | None = None,
    parent_thread_id: str | None = None,
    lane_role: str | None = None,
    bound_by: str | None = None,
    evidence: str | None = None,
    id: int | None = None,  # noqa: A002
    seq: int | None = None,
) -> dict[str, Any]:
    if id is not None or seq is not None:
        tokens = [name for name, val in (("id", id), ("seq", seq)) if val is not None]
        record("mcp.agentbus.lane_bind.rejected", reason="client_ordering_token")
        return {
            "error": (
                "agent-bus error: client-supplied ordering tokens not allowed: "
                + ", ".join(tokens)
            ),
            "reason": "client_ordering_token",
        }
    resolved_thread = _normalize_thread(thread) or _normalize_thread(thread_id)
    resolved_parent = (parent_thread or parent_thread_id or "").strip()
    role = (lane_role or "").strip()
    if not resolved_thread:
        record("mcp.agentbus.lane_bind.rejected", reason="thread_required")
        return {
            "error": "lane_bind: thread is required",
            "reason": "lane_bind_thread_required",
        }
    if not resolved_parent or not role:
        record("mcp.agentbus.lane_bind.rejected", reason="incomplete")
        return {
            "error": (
                "lane_bind: parent_thread and lane_role must both be supplied"
            ),
            "reason": "lane_bind_incomplete",
            "provided": [
                name
                for name, val in (
                    ("parent_thread", bool(resolved_parent)),
                    ("lane_role", bool(role)),
                )
                if val
            ],
        }
    return _lane_bind_impl(
        thread_id=resolved_thread,
        parent_thread_id=resolved_parent,
        lane_role=role,
        bound_by=bound_by,
        evidence=evidence,
    )


def _lane_current_dispatch(
    *,
    thread: str | int | None = None,
    thread_id: str | int | None = None,
) -> dict[str, Any]:
    resolved = _normalize_thread(thread) or _normalize_thread(thread_id)
    if not resolved:
        record("mcp.agentbus.lane_current.rejected", reason="thread_required")
        return {
            "error": "lane_current: thread is required",
            "reason": "lane_current_thread_required",
        }
    return _lane_current_impl(thread_id=resolved)
