"""Cortex tools — shorthand for common Cortex knowledge system writes.

Thin wrappers over cortex-api that reduce friction on the most frequent
write operations. Full cortex-api access remains available via local_api.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp_events import record

from .local_api import _relay

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_cortex_tools(mcp: FastMCP) -> None:
    """Register Cortex shorthand tools on the MCP server instance."""

    @mcp.tool()
    def cortex_assert(
        entity_id: str,
        claim: str,
        confidence: str,
        evidence: str,
        evidence_uris: list[str] | None = None,
    ) -> dict[str, Any]:
        """Seed an assertion into the Cortex knowledge system.

        Confidence levels:
            confirmed    — verified fact or settled decision
            believed     — working assumption, high confidence
            suspected    — pattern-based inference, not yet verified
            hypothesized — theory under investigation

        Evidence URI format:
            agent-bus:034           — thread by ID
            session:web-2026-03-16  — session journal
            doc:notes/system/...    — document path

        Args:
            entity_id: Target entity in type:slug format (e.g. 'doc:mcp-tools-catalog').
            claim: What was decided, discovered, or confirmed.
            confidence: One of confirmed / believed / suspected / hypothesized.
            evidence: Why — one sentence explaining the basis for the claim.
            evidence_uris: Optional list of source URIs linking back to evidence.

        Returns:
            Created assertion object, or {"error": "<message>"}.
        """
        valid_confidence = {"confirmed", "believed", "suspected", "hypothesized"}
        if confidence not in valid_confidence:
            return {
                "error": f"Invalid confidence {confidence!r}. "
                f"Must be one of: {sorted(valid_confidence)}"
            }

        body: dict[str, Any] = {
            "entity_id": entity_id,
            "claim": claim,
            "confidence": confidence,
            "evidence": evidence,
        }
        if evidence_uris:
            body["evidence_uris"] = evidence_uris

        result = _relay("cortex-api", "POST", "/assertions", body=body)

        if "error" in result:
            return {"error": f"cortex-api error: {result['error']}"}

        logger.info("cortex_assert: %s — %s (%s)", entity_id, claim[:60], confidence)
        record(
            "mcp.cortex.assertion.seeded",
            entity_id=entity_id,
            confidence=confidence,
        )
        return result
