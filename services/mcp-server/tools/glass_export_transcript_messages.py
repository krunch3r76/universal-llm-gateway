"""MCP read tool: ``glass_export_transcript_messages`` (Glass JSONL export)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from cursor_bridge.glass_export_messages import (
    GlassExportError,
    export_glass_transcript_messages,
    export_glass_transcript_messages_error_response,
)
from request_profile import current_request_metadata

if TYPE_CHECKING:
    from fastmcp import FastMCP


def _trusted_caller() -> str | None:
    meta = current_request_metadata()
    for key in ("caller_identity", "mcp_caller_identity", "oauth_client_id"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def register_glass_export_transcript_messages_tool(mcp: FastMCP) -> None:
    """Register read-only Glass transcript export (no BridgeOp send path)."""

    @mcp.tool(title="Glass Export Transcript Messages")
    def glass_export_transcript_messages(
        transcript_id: str,
        supervisor_work_key: str,
        tools: Literal["none", "marker"] = "marker",
        strict_dialogue: bool = False,
        from_byte_offset: int = 0,
    ) -> dict[str, Any]:
        """Export a Cursor Glass tab JSONL transcript as a continuity messages envelope.

        Read-only: no bridge send, steer, or seal. Requires ``GLASS_EXPORT_ALLOWED_AGENTS``.
        Available via: dispatch(tool="glass_export_transcript_messages", ...)
        """
        try:
            return export_glass_transcript_messages(
                transcript_id=transcript_id,
                supervisor_work_key=supervisor_work_key,
                trusted_caller=_trusted_caller(),
                tools=tools,
                strict_dialogue=strict_dialogue,
                from_byte_offset=from_byte_offset,
            )
        except GlassExportError as exc:
            return export_glass_transcript_messages_error_response(exc)
