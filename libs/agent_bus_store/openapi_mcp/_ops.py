"""Agent-bus dispatch op names — denominator for openapi_mcp stamping.

Must stay in sync with ``services/mcp-server/tools/agent_bus.AGENT_BUS_OPS``.
"""

from __future__ import annotations

AGENT_BUS_DISPATCH_OPS: frozenset[str] = frozenset(
    {
        "send",
        "request",
        "hop",
        "substrate_graph_write",
        "post",
        "reply",
        "fetch",
        "fetch_unread",
        "get",
        "threads",
        "thread_get",
        "create_thread",
        "close",
        "update_thread",
        "add_tags",
        "remove_tags",
        "update",
        "delete_thread",
        "delete_turn",
        "mark_read",
        "triage",
        "wait",
        "branch_associate",
        "branch_current",
    }
)
