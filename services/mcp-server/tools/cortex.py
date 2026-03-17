"""Cortex tools — shorthand for common Cortex knowledge system operations.

Thin wrappers over cortex-api that eliminate the need for raw sqlite_query
calls. All boot-sequence reads and session-end writes have dedicated tools here.
Full cortex-api access remains available via local_api for anything not covered.
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

    # ------------------------------------------------------------------ writes

    @mcp.tool()
    def cortex_assert(
        entity_id: str,
        claim: str,
        confidence: str,
        evidence: str,
        evidence_uris: list[str] | str | None = None,
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
            if isinstance(evidence_uris, str):
                evidence_uris = [evidence_uris]
            body["evidence_uris"] = [str(u) for u in evidence_uris]

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

    @mcp.tool()
    def cortex_journal_write(
        timestamp: str,
        agent: str,
        summary: str,
        domains: list[str] | None = None,
        decisions: list[str] | None = None,
        open_items: list[str] | None = None,
        file_path: str | None = None,
    ) -> dict[str, Any]:
        """Create a session journal row in Cortex.

        Replaces raw sqlite_execute calls for session-end journaling.
        Write the full narrative file separately via files(op='write').

        Args:
            timestamp: ISO timestamp string for the session start (e.g. '2026-03-17T05:09:00Z').
            agent: Agent name — 'web', 'cursor', or 'api'.
            summary: Narrative summary of what happened this session.
            domains: Optional list of domain tags (e.g. ['legal', 'infrastructure']).
            decisions: Optional list of decisions made this session.
            open_items: Optional list of items carried forward.
            file_path: Optional path to the full narrative journal file.

        Returns:
            Created session journal row, or {"error": "<message>"}.
        """
        body: dict[str, Any] = {
            "timestamp": timestamp,
            "agent": agent,
            "summary": summary,
        }
        if domains is not None:
            body["domains"] = domains
        if decisions is not None:
            body["decisions"] = decisions
        if open_items is not None:
            body["open_items"] = open_items
        if file_path is not None:
            body["file_path"] = file_path

        result = _relay("cortex-api", "POST", "/session-journals", body=body)

        if "error" in result:
            return {"error": f"cortex-api error: {result['error']}"}

        logger.info("cortex_journal_write: %s agent=%s", timestamp, agent)
        return result

    # ------------------------------------------------------------------- reads

    @mcp.tool()
    def cortex_deadlines() -> dict[str, Any]:
        """Return active legal deadlines from the Cortex knowledge graph.

        Used at boot to surface time-sensitive legal matters before anything else.

        Returns:
            DeadlineList with matter_id, matter_name, deadline_name, deadline_date,
            deadline_description fields per item, or {"error": "<message>"}.
        """
        result = _relay("cortex-api", "GET", "/deadlines")
        if "error" in result:
            return {"error": f"cortex-api error: {result['error']}"}
        return result

    @mcp.tool()
    def cortex_entities(
        type: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List Cortex entities, optionally filtered by type.

        Common types: person, organization, legal_matter, event, decision,
        document, deadline, property, discovery.

        Args:
            type: Optional entity type filter.
            limit: Maximum results (1–500, default 50).

        Returns:
            EntityList with id, type, name, created_at per item,
            or {"error": "<message>"}.
        """
        params = [f"limit={limit}"]
        if type is not None:
            params.append(f"type={type}")
        qs = "&".join(params)
        result = _relay("cortex-api", "GET", f"/entities?{qs}")
        if "error" in result:
            return {"error": f"cortex-api error: {result['error']}"}
        return result

    @mcp.tool()
    def cortex_entity_get(entity_id: str) -> dict[str, Any]:
        """Fetch a single Cortex entity with all linked assertions.

        Args:
            entity_id: Entity ID in type:slug format (e.g. 'person:kaywan-mansubi').

        Returns:
            EntityDetail with full attributes and assertions list,
            or {"error": "<message>"}.
        """
        result = _relay("cortex-api", "GET", f"/entities/{entity_id}")
        if "error" in result:
            return {"error": f"cortex-api error: {result['error']}"}
        return result

    @mcp.tool()
    def cortex_assertions(
        entity_id: str | None = None,
        confidence: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List Cortex assertions, optionally filtered by entity and/or confidence.

        Used at boot to surface open investigative threads (suspected/hypothesized).

        Args:
            entity_id: Filter to assertions on a specific entity.
            confidence: Filter by confidence level — confirmed, believed,
                suspected, or hypothesized. Omit to return all.
            limit: Maximum results (1–500, default 50).

        Returns:
            AssertionList with id, entity_id, claim, confidence, evidence,
            evidence_uris, created_at per item, or {"error": "<message>"}.
        """
        params = [f"limit={limit}"]
        if entity_id is not None:
            params.append(f"entity_id={entity_id}")
        if confidence is not None:
            params.append(f"confidence={confidence}")
        qs = "&".join(params)
        result = _relay("cortex-api", "GET", f"/assertions?{qs}")
        if "error" in result:
            return {"error": f"cortex-api error: {result['error']}"}
        return result

    @mcp.tool()
    def cortex_journal_read(limit: int = 3) -> dict[str, Any]:
        """Return recent session journals in reverse insertion order.

        Used at boot to restore narrative continuity from prior sessions.

        Args:
            limit: Number of journals to return (1–100, default 3).

        Returns:
            SessionJournalList with id, timestamp, agent, summary, domains,
            decisions, open_items, file_path per item, or {"error": "<message>"}.
        """
        result = _relay("cortex-api", "GET", f"/session-journals?limit={limit}")
        if "error" in result:
            return {"error": f"cortex-api error: {result['error']}"}
        return result
