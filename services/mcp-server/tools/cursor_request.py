"""Narrow cursor-auto request lane — dedicated MCP tool for approval gating.

The web-claude MCP harness gates tool approval at *registered-tool* granularity,
not at the ``arguments.tool`` sub-op level. Because the unified ``agent_bus``
tool bundles destructive ops (``delete_thread``, ``close``, ``triage``) with
``request``, an operator cannot write an allow-by-name rule that covers only
the sanctioned unattended cursor-auto lane. This module registers
``cursor_request``, exposing ONLY the ``request`` op and delegating to
``_request_dispatch`` — no logic is duplicated.

Registered on life and code surfaces alongside ``agent_bus_read``.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from mcp_events import record
from mcp_toolprogress import toolprogress_begin, toolprogress_end

from ._agent_bus_author import reconcile_author_arguments
from .agent_bus import _request_dispatch

# Cached once at import — runtime inspect.signature breaks under test mocks.
_REQUEST_DISPATCH_PARAMS: frozenset[str] = frozenset(
    inspect.signature(_request_dispatch).parameters
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

# Caller-facing wire fields only — ``to`` is fixed to ``cursor``; no tool/op discriminator.
CALLER_FIELDS: frozenset[str] = frozenset(
    {
        "thread",
        "new_slug",
        "subject",
        "body",
        "contract",
        "desired_model",
        "desired_effort",
        "from_agent",
        "from",
        "summary",
        "tags",
        "sidecar_content",
        "sidecar_slug",
        "require_attended",
        "after_turn",
    }
)


def _unknown_caller_error(unknown: list[str]) -> dict[str, Any]:
    return {
        "error": (
            f"cursor_request: unsupported argument(s): "
            f"{', '.join(sorted(unknown))}. "
            f"Accepted: {sorted(CALLER_FIELDS)}"
        ),
    }


def _dispatch_cursor_request(parsed: dict[str, Any]) -> Any:
    """Validate caller dict, reconcile author, delegate to ``_request_dispatch``."""
    unknown = [k for k in parsed if k not in CALLER_FIELDS]
    if unknown:
        record(
            "mcp.agentbus.dispatch.rejected",
            tool="request",
            surface="cursor_request",
            unknown=",".join(sorted(unknown)),
        )
        return _unknown_caller_error(unknown)

    parsed, author_error = reconcile_author_arguments(parsed)
    if author_error is not None:
        record(
            "mcp.agentbus.dispatch.rejected",
            tool="request",
            surface="cursor_request",
            reason=str(author_error.get("reason", "")),
        )
        return author_error

    accepted_dispatch = _REQUEST_DISPATCH_PARAMS
    dispatch_kwargs = {k: v for k, v in parsed.items() if k in accepted_dispatch}
    dispatch_kwargs["to"] = "cursor"
    record("mcp.agentbus.dispatch", tool="request", surface="cursor_request")
    return _request_dispatch(**dispatch_kwargs)


def register_cursor_request_tool(mcp: FastMCP) -> None:
    """Register the narrow ``cursor_request`` tool on the MCP server."""

    @mcp.tool(title="Cursor Auto Request")
    def cursor_request(
        subject: str,
        body: str,
        new_slug: str | None = None,
        thread: str | None = None,
        from_agent: str = "",
        summary: str | None = None,
        tags: list[str] | None = None,
        sidecar_content: str | None = None,
        sidecar_slug: str | None = None,
        desired_model: str = "auto",
        desired_effort: str = "medium",
        contract: str = "answer",
        require_attended: bool = False,
        after_turn: int = 0,
    ) -> Any:
        """Sanctioned unattended cursor-auto request lane (agent_bus request only).

        Posts a directive to the Cursor Auto handler. Recipient ``to`` is always
        ``cursor`` — it is not a caller parameter. Exactly one of ``new_slug``
        (new thread) or ``thread`` (continue) is required.

        ``contract`` must be one of: ``answer`` | ``confer`` | ``investigate`` |
        ``implement`` | ``verify`` | ``execute`` | ``propagate``. Unknown values
        are rejected before the turn is written. ``execute`` runs one
        manifest-allowlisted tier-M tool op in seat (body needs ``tool_op:`` +
        ``effects_expected:``). ``propagate`` requests drain-gated service
        restart (``effects_expected:`` + propagation YAML or
        ``scope: propagation sync_restart <service>``).

        Returns ``{thread, turn, handler_status, poll_hint}``. Poll completion
        via ``agent_bus(tool="wait", arguments=poll_hint)`` — not a client loop.

        Author: prefer ``from_agent=``; surface autofill on ``/mcp/life`` or
        ``/mcp/code`` when omitted (``web-anthropic`` or ``cursor`` respectively).
        """
        t_prog, prog_timer = toolprogress_begin("cursor_request")
        err: str | None = None
        try:
            parsed: dict[str, Any] = {
                "subject": subject,
                "body": body,
                "desired_model": desired_model,
                "desired_effort": desired_effort,
                "contract": contract,
                "require_attended": require_attended,
                "after_turn": after_turn,
            }
            if new_slug is not None:
                parsed["new_slug"] = new_slug
            if thread is not None:
                parsed["thread"] = thread
            if from_agent:
                parsed["from_agent"] = from_agent
            if summary is not None:
                parsed["summary"] = summary
            if tags is not None:
                parsed["tags"] = tags
            if sidecar_content is not None:
                parsed["sidecar_content"] = sidecar_content
            if sidecar_slug is not None:
                parsed["sidecar_slug"] = sidecar_slug
            return _dispatch_cursor_request(parsed)
        except Exception as exc:
            err = str(exc)
            raise
        finally:
            toolprogress_end(t_prog, prog_timer, "cursor_request", error=err)
