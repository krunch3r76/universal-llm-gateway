"""Read-only companion to the unified ``cortex`` dispatch tool.

Strict positive allow-list at the companion boundary (incl. family-None registry
mutators), then ``endpoint_op_allowed(..., \"cortex\", tool)`` for census ops.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from mcp.types import ToolAnnotations
from mcp_events import record
from mcp_toolprogress import toolprogress_begin, toolprogress_end
from request_profile import current_request_metadata
from tool_access import endpoint_op_allowed

from ._agent_tools import (
    JsonArgStr,
    dispatch_arguments_error,
    parse_dispatch_arguments,
)
from ._cortex_relay import cx

if TYPE_CHECKING:
    from fastmcp import FastMCP

Surface = Literal["life", "code"]

# Derived once at implement start (G3): read-family ∩ derive_cortex_surface(surface),
# plus activate, minus cortex_brief. Pinned — tests compare runtime to these sets.
CORTEX_READ_OPS_LIFE: frozenset[str] = frozenset(
    {
        "activate",
        "analyze_impact",
        "assertion_get",
        "assertion_state",
        "assertions",
        "case_audit",
        "deadlines",
        "edge_traverse",
        "edge_types",
        "edges",
        "entities",
        "entities_by_content_hash",
        "entity_get",
        "fill_gaps",
        "frictions",
        "impact",
        "journal_read",
        "relationships",
        "render_subgraph",
        "resolve",
        "resolve_assertion_chunk",
        "review_queue",
        "rj_list",
        "rj_read",
        "search",
        "stats",
        "surface_forms",
        "tag_list",
        "todo_candidates",
        "walk_subgraph",
    }
)
CORTEX_READ_OPS_CODE: frozenset[str] = frozenset(CORTEX_READ_OPS_LIFE)

CORTEX_READ_OPS_BY_SURFACE: dict[Surface, frozenset[str]] = {
    "life": CORTEX_READ_OPS_LIFE,
    "code": CORTEX_READ_OPS_CODE,
}


def cortex_read_admitted_ops(surface: Surface) -> frozenset[str]:
    return CORTEX_READ_OPS_BY_SURFACE[surface]


def _not_a_read_op(tool: str) -> dict[str, str]:
    return {
        "error": (
            f"cortex_read: {tool!r} is not a read-only op. "
            "For mutating or session ops use the cortex tool."
        ),
        "reason": "not_a_read_op",
    }


def register_cortex_read_tool(mcp: FastMCP, surface: Surface = "life") -> None:
    """Register read-only ``cortex_read`` dispatch tool."""

    admitted = cortex_read_admitted_ops(surface)

    @mcp.tool(
        title="Cortex (read-only)",
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    def cortex_read(tool: str, arguments: JsonArgStr = "{}") -> Any:
        """Read-only cortex ops — auto-approvable companion to cortex."""
        op = str(tool or "").strip()
        if op not in admitted:
            record(
                "mcp.cortex_read.rejected",
                tool=op,
                reason="not_a_read_op",
                surface=surface,
            )
            return _not_a_read_op(op or tool)

        if parse_dispatch_arguments(arguments) is None:
            return dispatch_arguments_error(
                arguments, example='{"entity_id": "type:slug"}', tool="cortex_read"
            )

        meta = current_request_metadata()
        req_surface = str(meta.get("surface") or surface)
        seat = (
            meta.get("seat_class")
            or meta.get("caller_identity")
            or meta.get("request_profile")
        )

        # Double guard for census-family ops only — not sufficient for family-None.
        allowed, rejection = endpoint_op_allowed(req_surface, "cortex", op)
        if not allowed and rejection is not None:
            return {
                "error": rejection["hint"],
                "status_code": rejection["status_code"],
                "family": rejection["family"],
                "surface": rejection["surface"],
            }

        t_prog, prog_timer = toolprogress_begin("cortex_read", inner_tool=op)
        err: str | None = None
        try:
            body: dict[str, Any] = {
                "tool": op,
                "arguments": arguments,
                "surface": req_surface,
                "via_adapter": True,
            }
            if seat:
                body["seat"] = str(seat)
            headers: dict[str, str] = {
                "X-ULG-Surface": req_surface,
                "X-ULG-Adapter": "mcp-server",
            }
            if seat:
                headers["X-ULG-Seat"] = str(seat)
            caller = meta.get("caller_identity")
            if caller:
                headers["X-ULG-Caller"] = str(caller)
            session_id = meta.get("mcp_session_id")
            if session_id:
                headers["X-ULG-Session"] = str(session_id)
            return cx(
                "POST",
                "/dispatch",
                body,
                headers=headers,
                dispatch_tool=op,
            )
        except Exception as exc:
            err = str(exc) or type(exc).__name__
            return {"error": str(exc)}
        finally:
            toolprogress_end(t_prog, prog_timer, "cortex_read", error=err, inner_tool=op)
