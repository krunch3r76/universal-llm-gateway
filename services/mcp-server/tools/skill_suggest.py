"""skill_suggest MCP tool — deprecated tombstone (hidden + rejecting)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp import FastMCP

_DEPRECATION_ERROR = (
    "skill_suggest is deprecated indefinitely — do not call it. "
    "Discover skills via native boot index / <available_skills> stubs / "
    "description-gated rules only."
)


def register_skill_suggest_tools(mcp: FastMCP) -> None:
    """Register a rejecting tombstone; hidden from tools/list and tool_search."""

    @mcp.tool(title="Skill Suggest")
    def skill_suggest(
        loaded: list[str] | str | None = None,
        conversation_context: str | None = None,
        limit: int | None = None,
        agent: str | None = None,
        entity_ids: list[str] | str | None = None,
        prefer_worker: bool | None = None,
    ) -> dict[str, Any]:
        """Deprecated — always rejects. Use native skill discovery instead."""
        _ = (
            loaded,
            conversation_context,
            limit,
            agent,
            entity_ids,
            prefer_worker,
        )
        return {"error": _DEPRECATION_ERROR}
