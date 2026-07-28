"""``vision_digest`` MCP tool — thin GET relay to cortex-api."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools._cortex_relay import cx

if TYPE_CHECKING:
    from fastmcp import FastMCP

_VISION_DIGEST_PATH = "/api/v1/doctrine/vision-digest"


def register_vision_digest_tools(mcp: FastMCP) -> None:
    """Register the posture-stack vision digest relay tool."""

    @mcp.tool(title="Vision Digest")
    def vision_digest() -> dict[str, Any]:
        """Fetch the live posture-stack vision digest from cortex-api.

        Available via: dispatch(tool="vision_digest", ...)
        """
        return cx("GET", _VISION_DIGEST_PATH)
