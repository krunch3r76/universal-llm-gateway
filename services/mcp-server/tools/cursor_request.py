"""Life-primary ``operator_request`` tombstone (a:38728).

``cursor_request`` is no longer registered. Every ``operator_request`` call
returns ``cursor_auto_retired_refusal`` and does not call ``_request_dispatch``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .agent_bus.request import cursor_auto_retired_refusal

if TYPE_CHECKING:
    from fastmcp import FastMCP

_RETIRED_DOC = (
    "RETIRED (a:38728). Refuses every call. "
    "Code commission: team_dispatch on ulg-code. "
    "Life CSE: life_dispatch. A bus turn: agent_bus send."
)


def register_cursor_request_tool(mcp: FastMCP) -> None:
    """Register the retired ``operator_request`` tombstone on the life surface."""

    def operator_request(
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
        desired_effort: str = "auto",
        escalation: str | None = None,
        contract: str = "answer",
        require_attended: bool = False,
        after_turn: int = 0,
        lane: str | None = None,
        workspace: str | None = None,
        parent_thread: str | None = None,
        lane_role: str | None = None,
        request_id: str | None = None,
        cse_registration_id: str | None = None,
        cse_chat_url: str | None = None,
    ) -> Any:
        """RETIRED (a:38728). Refuses every call."""
        del (
            subject,
            body,
            new_slug,
            thread,
            from_agent,
            summary,
            tags,
            sidecar_content,
            sidecar_slug,
            desired_model,
            desired_effort,
            escalation,
            contract,
            require_attended,
            after_turn,
            lane,
            workspace,
            parent_thread,
            lane_role,
            request_id,
            cse_registration_id,
            cse_chat_url,
        )
        return cursor_auto_retired_refusal()

    operator_request.__doc__ = _RETIRED_DOC
    mcp.tool(
        title="Operator Request",
        description=_RETIRED_DOC,
    )(operator_request)
