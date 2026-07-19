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
        if "error" in result:
            logger.error(
                "cortex_staging_reject failed for ID %d: %s",
                staging_id,
                result.get("error"),
            )
        else:
            logger.info("cortex_staging_reject: %d", staging_id)
        return result

    @mcp.tool(title="Cortex: Batch Approve Staging")
    def cortex_staging_batch_approve(
        staging_ids: list[int],
        ledger_id: int | None = None,
        reviewer: str = "web-anthropic-opus-review",
    ) -> dict[str, Any]:
        """Approve a digest revision batch (supersedes/retracts before adds).

        Args:
            staging_ids: Pending extraction_staging row ids from revision_staged.
            ledger_id: digest_ledger row id to mark committed after apply.
            reviewer: Provenance label for the review seat (default opus review).

        Returns:
            StagingList of approved items, or {"error": "<message>"}.
        """
        body: dict[str, Any] = {
            "staging_ids": staging_ids,
            "reviewer": reviewer,
        }
        if ledger_id is not None:
            body["ledger_id"] = ledger_id
        result = cx("POST", "/staging/batch-approve", body)
        if "error" in result:
            logger.error(
                "cortex_staging_batch_approve failed: %s",
                result.get("error"),
            )
        else:
            logger.info(
                "cortex_staging_batch_approve: ids=%s ledger_id=%s",
                staging_ids,
                ledger_id,
            )
        return result
