"""X Direct Message MCP tool — read (and optional send) for @KJMXYXX."""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from tools._x_dm_client import fetch_messages, list_conversations, send_message

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)


def register_x_dm_tools(mcp: FastMCP) -> None:
    """Register the x_dm tool on *mcp*."""

    @mcp.tool(title="X Direct Messages")
    def x_dm(
        op: str,
        conversation_id: str | None = None,
        limit: int = 50,
        since_id: str | None = None,
        pagination_token: str | None = None,
        text: str | None = None,
        max_pages: int = 5,
    ) -> dict:
        """Read or send Direct Messages for @KJMXYXX via the X API.

        Requires OAuth 1.0a user credentials in the MCP container:
        X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET.

        Ops:
          list — scan recent DM events and summarize conversation IDs
          fetch — read messages (all recent DMs, or one conversation_id)
          send — post text into an existing conversation (DM or group)

        Notes:
          - Ephemeral: nothing is written to Cortex or agent-bus.
          - API exposes legacy DMs only (~30 day window).
          - Encrypted X Chat (x.com/i/chat) is NOT available via API.
          - Use list first to discover conversation_id values.

        Examples:
          x_dm(op="list")
          x_dm(op="fetch", limit=30)
          x_dm(op="fetch", conversation_id="2815754953-2020227357030907904")
          x_dm(op="send", conversation_id="...", text="Hello — PharmD")
        """
        match op.strip().lower():
            case "list":
                result = list_conversations(max_pages=max_pages)
            case "fetch":
                result = fetch_messages(
                    conversation_id=conversation_id,
                    limit=limit,
                    since_id=since_id,
                    pagination_token=pagination_token,
                )
            case "send":
                if not conversation_id:
                    return {"error": "conversation_id is required for op=send"}
                if not text:
                    return {"error": "text is required for op=send"}
                result = send_message(conversation_id=conversation_id, text=text)
            case _:
                return {
                    "error": f"Unknown op {op!r}. Use list, fetch, or send.",
                }

        if "error" not in result:
            logger.info(
                "x_dm op=%s conversation_id=%s count=%s",
                op,
                conversation_id,
                result.get("count", result.get("pages_scanned")),
            )
        return result
