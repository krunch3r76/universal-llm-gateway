"""skill_suggest MCP tool — thin relay to cortex-api POST /skills/suggest."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_seat.profiles import known_seats
from agent_seat.registry import normalize_agent_slug
from request_profile import current_request_metadata

from tools._cortex_relay import cx

if TYPE_CHECKING:
    from fastmcp import FastMCP


def _resolve_effective_agent(agent: str | None) -> str | None:
    if agent and str(agent).strip():
        return normalize_agent_slug(str(agent).strip())

    meta = current_request_metadata()
    caller_identity = str(meta.get("caller_identity") or "").strip()
    profile = str(meta.get("request_profile") or meta.get("profile") or "").strip()

    if caller_identity:
        canonical = normalize_agent_slug(caller_identity)
        if canonical in known_seats():
            return canonical

    if profile == "cursor_safe":
        return "claude-cursor"

    seat_class = str(meta.get("seat_class") or "").strip()
    if seat_class == "claude" and profile == "default":
        return "claude-web"

    return None


def register_skill_suggest_tools(mcp: FastMCP) -> None:
    """Register the skill_suggest thin relay tool."""

    @mcp.tool(title="Skill Suggest")
    def skill_suggest(
        loaded: list[str],
        conversation_context: str | None = None,
        limit: int | None = None,
        agent: str | None = None,
        rerank: bool | None = None,
    ) -> dict[str, Any]:
        """Suggest newly relevant, not-yet-loaded skills for the caller seat.

        Returns ranked skill-slug deltas with concise reasons. The server injects
        the caller seat when ``agent`` is omitted; pass ``agent`` explicitly when
        seat resolution is unavailable.
        """
        effective_agent = _resolve_effective_agent(agent)
        if not effective_agent:
            return {
                "error": (
                    "agent seat could not be resolved from session context; "
                    "pass agent=<seat-slug> explicitly (e.g. claude-cursor, claude-web)"
                )
            }

        payload: dict[str, Any] = {
            "agent": effective_agent,
            "loaded": loaded,
        }
        if conversation_context is not None:
            payload["conversation_context"] = conversation_context
        if limit is not None:
            payload["limit"] = limit
        if rerank is not None:
            payload["rerank"] = rerank

        return cx(
            "POST",
            "/skills/suggest",
            payload,
            headers={"X-Cortex-Transport": "mcp"},
        )
