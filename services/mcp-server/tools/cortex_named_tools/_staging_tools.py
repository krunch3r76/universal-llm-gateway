"""Staging proposal MCP tool registrations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from .._cortex_relay import cx

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_staging_tools(mcp: FastMCP) -> None:
    """Register staging tools on *mcp*."""

    @mcp.tool(title="Cortex: List Staging")
    def cortex_staging_list(
        status: str | None = None,
        source_uri: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List staging proposals with optional filters.

        Args:
            status: Filter — pending, approved, rejected, merged.
            source_uri: Filter by source URI.
            limit: Maximum results (1-500, default 50).

        Returns:
            StagingList, or {"error": "<message>"}.
        """
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status
        if source_uri is not None:
            params["source_uri"] = source_uri
        return cx("GET", f"/staging?{urlencode(params)}")

    @mcp.tool(title="Cortex: Reject Staging")
    def cortex_staging_reject(
        staging_id: int, reviewer: str = "cursor"
    ) -> dict[str, Any]:
        """Reject a staging proposal.

        Args:
            staging_id: The staging proposal ID.
            reviewer: Who rejected (default 'cursor').

        Returns:
            Updated StagingItem, or {"error": "<message>"}.
        """
        result = cx("POST", f"/staging/{staging_id}/reject", {"reviewer": reviewer})
        if "error" not in result:
            logger.error(
                "cortex_staging_reject failed for ID %d: %s",
                staging_id,
                result.get("error"),
            )
        else:
            logger.info("cortex_staging_reject: %d", staging_id)
        return result
