"""Boot and session-close MCP tool registrations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp_events import record

from ._boot_runner import run_cortex_boot

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_orchestration_tools(mcp: FastMCP) -> None:
    """Register boot and session-close tools on *mcp*."""

    @mcp.tool(title="Cortex Boot")
    def cortex_boot(
        agent: str = "cursor",
        transcript_id: str = "",
    ) -> dict[str, Any]:
        """Slim boot briefing for session start. Returns a compact briefing card
        (~5-10KB) with priority signals and a section manifest for on-demand pulls.

        The briefing card contains: deadlines, unread bus summary, review queue
        count, last session summary, top todos, self-observations, and temporal
        alerts. Heavy data (full sessions, assertions, gated entities, file
        contents) is NOT inlined — pull on demand via manifest hints.

        Args:
          agent         — agent profile: cursor, web, api, api_claude, oppie, orion, subagent (default: "cursor")
          transcript_id — if provided, loads continuation context for that transcript

        Key response fields:
          session_id             — server-minted ID; hold for entire session
          briefing_card          — compact Markdown briefing (~3-5KB)
          sections_available     — manifest of deeper-pull sections with fetch hints
          operational_context_ref — path to operational context file (read on demand)
        """
        return run_cortex_boot(agent=agent, transcript_id=transcript_id)

    @mcp.tool(title="Session Close (Reminder)")
    def session_close(
        agent: str = "cursor",
        session_id: str = "",
    ) -> dict[str, Any]:
        """DEPRECATED — use cortex(tool="session_close", ...) for atomic closes.

        This tool only returns step-by-step instructions without performing
        the close.  The atomic version (cortex dispatch) validates transcript
        content, writes the file, and creates entity + journal row + edge
        in one call.

        Kept for backward compatibility.  Will be removed in a future release.

        Args:
          agent      — agent identity: cursor, web, api (default: "cursor")
          session_id — session ID from boot (if empty, mints one from current UTC)
        """
        from .._session_close import build_session_close

        result = build_session_close(agent=agent, session_id=session_id)
        if "error" not in result:
            result["_deprecation"] = (
                "This tool is deprecated. Use cortex(tool='session_close', "
                'arguments=\'{"session_id": "...", "agent": "...", '
                '"transcript_md": "...", "summary": "..."}\') instead. '
                "The atomic version prevents stub-only closes."
            )
            record(
                "mcp.session.close",
                agent=agent,
                transcript_id=result.get("transcript_id"),
            )
        return result
