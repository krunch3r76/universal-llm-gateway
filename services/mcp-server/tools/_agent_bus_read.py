"""Read-only projection of the agent_bus surface for approval-gating clients.

The web-claude MCP harness gates tool approval at *registered-tool* granularity,
not at the ``arguments.tool`` sub-op level. Because the unified ``agent_bus``
dispatch tool bundles read ops (fetch/threads/get/wait) with mutating ops
(post/reply/delete_*), the harness cannot advertise it ``readOnlyHint`` and so
every call — including reads — requires manual approval (agent-bus thread 1241).

This module registers a second tool, ``agent_bus_read``, exposing ONLY the
read subset and carrying ``ToolAnnotations(readOnlyHint=True)`` so the harness
auto-approves reads. All ops delegate to the same ``_*_dispatch`` impls in
``agent_bus.py`` — no logic is duplicated. Mutating ops stay on ``agent_bus``.

Claude/web surface only: registered on the main life/code FastMCP instance.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations
from mcp_events import record
from mcp_toolprogress import toolprogress_begin, toolprogress_end

from ._agent_tools import JsonArgStr
from .agent_bus import (
    _fetch_dispatch,
    _fetch_unread_dispatch,
    _get_dispatch,
    _threads_dispatch,
    _wait_dispatch,
)
from .agent_bus.lane_associations import _lane_current_dispatch
from .agent_bus.threads import _job_state_dispatch, _thread_get_dispatch

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

# Read-only subset of AGENT_BUS_OPS. wait() is a server-side block with no
# mutation, so it belongs here. Mutating/destructive ops are intentionally
# absent — callers use the unified agent_bus tool for those.
AGENT_BUS_READ_OPS: dict[str, Callable[..., Any]] = {
    "fetch": _fetch_dispatch,
    "fetch_unread": _fetch_unread_dispatch,
    "get": _get_dispatch,
    "thread_get": _thread_get_dispatch,
    "threads": _threads_dispatch,
    "job_state": _job_state_dispatch,
    "wait": _wait_dispatch,
    "lane_current": _lane_current_dispatch,
}


def register_agent_bus_read_tool(mcp: FastMCP) -> None:
    """Register the read-only ``agent_bus_read`` dispatch tool on the MCP server."""

    @mcp.tool(
        title="Agent Bus (read-only)",
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    def agent_bus_read(tool: str, arguments: JsonArgStr = "{}") -> Any:
        """Read-only agent-bus ops — auto-approvable companion to agent_bus.

        Same calling convention as agent_bus: tool=<op>, arguments=<json>.
        Exposes ONLY non-mutating ops so approval-gating harnesses can
        auto-approve reads. For post/reply/update/close/delete_* use agent_bus.

        Operations (identical semantics to the matching agent_bus ops):
          thread_get   (thread)  — single ThreadDetail (+ cursor_auto_job when a non-terminal Auto job is on the lane)
          threads      (status?, tags?, lifecycle_state?, last?, has_unread?, query?)
          job_state    (thread|thread_id?, job_id?, include_terminal?)  — keyed cursor-auto phase+clocks
          fetch        (to?, thread?, last?, unread?, compact?, mark_read?, all?)
          fetch_unread (to?, thread?, mark_read?, compact?, active_since?, limit?, all?)  — recipient scope: enriched per-thread unread digest; thread scope: that thread's full unread turn list
          get          (thread, turn_number)  — turn_number may be int or "latest"
          wait         (thread, after_turn?, wait_seconds?, completion?, from_agent?)
          lane_current (thread) — derived current lane parentage (state=none when unbound)

        Note: mark_read=true mutates per-turn read pointers, not thread/turn
        content; it is permitted here as a read-cursor side effect.
        """
        from ._agent_tools import (
            dispatch_arguments_error,
            parse_dispatch_arguments,
        )

        handler = AGENT_BUS_READ_OPS.get(tool)
        if handler is None:
            return {
                "error": (
                    f"agent_bus_read: {tool!r} is not a read-only op. "
                    f"Available: {sorted(AGENT_BUS_READ_OPS.keys())}. "
                    "For mutating ops (post, reply, update, close, "
                    "delete_turn, delete_thread) use the agent_bus tool."
                ),
                "reason": "not_a_read_op",
            }
        t_prog, prog_timer = toolprogress_begin("agent_bus_read", inner_tool=tool)
        err: str | None = None
        try:
            parsed = parse_dispatch_arguments(arguments)
            if parsed is None:
                return dispatch_arguments_error(arguments, example='{"thread": "111"}')
            accepted = set(inspect.signature(handler).parameters)
            unknown = [k for k in parsed if k not in accepted]
            if unknown:
                record(
                    "mcp.agentbus.dispatch.rejected",
                    tool=tool,
                    surface="read",
                    unknown=",".join(sorted(unknown)),
                )
                return {
                    "error": (
                        f"{tool}: unsupported argument(s): "
                        f"{', '.join(sorted(unknown))}. "
                        f"Accepted: {sorted(accepted)}"
                    )
                }
            record("mcp.agentbus.dispatch", tool=tool, surface="read")
            return handler(**parsed)
        except Exception as exc:
            err = str(exc)
            raise
        finally:
            toolprogress_end(
                t_prog,
                prog_timer,
                "agent_bus_read",
                error=err,
                inner_tool=tool,
            )
