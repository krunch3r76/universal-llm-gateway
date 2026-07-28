"""MCP tool registration for the email domain dispatch surface.

Defines ``register_email_tools`` which installs the single ``email`` tool on a
FastMCP instance. The tool is a thin JSON-argument dispatcher:

- parses ``op`` and ``arguments`` (JSON string)
- validates ``op`` against the catalog
- looks up the handler in the private ``OPS`` table
- falls back to ``not_yet`` for catalogued but unimplemented ops
- records structured dispatch events via ``mcp_events.record``

This module is the only place that may import ``universal_logging`` for the
email package. The original monolithic module used stdlib ``logging.getLogger``;
that violation is corrected here by using the workspace-standard
``get_logger`` factory. No other file in the package may import stdlib logging.
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING, Any

from mcp_events import record
from tools.local._email_catalog import CATALOG

from .surface_op_guard import (
    current_mcp_surface,
    email_op_allowed_on_surface,
    life_surface_op_denial,
)
from universal_logging import get_logger

from .catalog_operations import not_yet
from .operation_registry import OPS

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)


def register_email_tools(mcp: FastMCP) -> None:
    """Register the email domain dispatch tool on the MCP server instance."""

    @mcp.tool(title="Email")
    def email(op: str = "list", arguments: str = "{}") -> Any:
        """Email — mailbox access, ingestion pipelines, and email management.

        Relay to the email-bridge service. Use ``op="list"`` to discover
        available operations, their safety tiers, and implementation status.

        op: operation name (call with op="list" for full catalog)
        arguments: JSON string with operation arguments

        Safety tiers: R=read, M=mutation, I=ingest, D=draft, O=outbound.
        Only tier O (send) requires human confirmation.

        Via dispatch (overflow): nest op-specific params inside the ``arguments`` string:
          dispatch(tool="email", arguments='{"op": "recent", "arguments": "{\\"mailbox\\": \\"Sent\\", \\"limit\\": 20}"}')
          dispatch(tool="email", arguments='{"op": "review_extract", "arguments": "{\\"message_id\\": \\"<msg-id>\\"}"}')

        Operations:
          list () — op catalog with tiers and status
          status () — bridge health and counts
          get (message_id) — full rendered email
          recent (mailbox?, limit?) — recent messages, optional folder
          search (sender?, subject?, mailbox?, limit?) — indexed search
          archive (mailbox?) — list pulled emails
          pull (mode?, mailbox?) — IMAP pull
          pending () — awaiting capture
          ingest_one (message_id) — capture-only ingest
          review_extract (message_id, correspondence_id?) — probate extraction
          review_dismiss (message_id, reason?) — dismiss without extraction
          move (message_ids, folder) — IMAP move after capture
          create_folder (folder) — create IMAP folder before move (idempotent)
          retry (message_ids) — re-capture failed ids

        Examples:
          email(op="list")
          email(op="recent", arguments='{"mailbox": "Sent", "limit": 20}')
          email(op="get", arguments='{"message_id": "<msg-id>"}')
          email(op="review_extract", arguments='{"message_id": "<msg-id>"}')
          email(op="create_folder", arguments='{"folder": "Processed"}')
        """
        if op not in CATALOG:
            return {
                "error": f"Unknown email op {op!r}. Use op='list' for available operations.",
                "available": sorted(OPS),
            }

        if not email_op_allowed_on_surface(op):
            return life_surface_op_denial(op)

        handler = OPS.get(op)
        if handler is None:
            return not_yet(op)

        try:
            args = _json.loads(arguments)
            if not isinstance(args, dict):
                return {
                    "error": f"arguments must be a JSON object, got {type(args).__name__}"
                }
        except _json.JSONDecodeError as exc:
            return {"error": f"Invalid arguments JSON: {exc}"}

        record("mcp.email.dispatch", op=op)
        result = handler(**args)
        if isinstance(result, dict) and "error" in result:
            record("mcp.email.dispatch.error", op=op, error=result["error"])
        else:
            record("mcp.email.dispatch.success", op=op)
        return result
