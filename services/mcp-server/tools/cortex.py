"""Cortex dispatch tool — cortex(tool=..., arguments=...) surface for the Cortex knowledge system.

Routes entity, assertion, deadline, journal, staging, and review operations
through cortex-api via the local_api relay. Uses the same dispatch calling
convention as the primary dispatch() tool. Lower-frequency tools (chunks,
surface forms) remain in cortex_v2.py as dispatch-only.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from mcp_events import record

from .local_api import _relay

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def _cx(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Relay to cortex-api, normalizing error shape."""
    result = _relay("cortex-api", method, path, body=body)
    if "error" in result:
        return {"error": f"cortex-api error: {result['error']}"}
    return result


# ── op handlers ──────────────────────────────────────────────────────────


def _op_entities(
    type: str | None = None, limit: int | None = None, **_: object
) -> dict[str, Any]:
    params: dict[str, object] = {"limit": limit or 50}
    if type is not None:
        params["type"] = type
    return _cx("GET", f"/entities?{urlencode(params)}")


def _op_entity_get(entity_id: str | None = None, **_: object) -> dict[str, Any]:
    if not entity_id:
        return {"error": "entity_id is required"}
    return _cx("GET", f"/entities/{entity_id}")


_VALID_STATUS = {"confirmed", "provisional", "merged", "deprecated"}


def _op_entity_create(
    id: str | None = None,
    type: str | None = None,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
    notes: str | None = None,
    aliases: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
    source_uri: str | None = None,
    **_: object,
) -> dict[str, Any]:
    required_fields = {"id": id, "type": type, "name": name}
    for field, val in required_fields.items():
        if not val:
            return {"error": f"{field} is required"}
    if status is not None and status not in _VALID_STATUS:
        return {
            "error": f"Invalid status {status!r}. "
            f"Must be one of: {sorted(_VALID_STATUS)}"
        }
    body: dict[str, Any] = {
        "id": id,
        "type": type,
        "name": name,
        **({} if description is None else {"description": description}),
        **({} if status is None else {"status": status}),
        **({} if notes is None else {"notes": notes}),
        **({} if aliases is None else {"aliases": aliases}),
        **({} if attributes is None else {"attributes": attributes}),
        **({} if source_uri is None else {"source_uri": source_uri}),
    }
    result = _cx("POST", "/entities", body)
    if "error" not in result:
        logger.info("cortex entity_create: %s (%s)", id, type)
        record("mcp.cortex.entity.created", entity_id=id, entity_type=type)
    return result


def _op_entity_update(
    entity_id: str | None = None,
    description: str | None = None,
    status: str | None = None,
    notes: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if not entity_id:
        return {"error": "entity_id is required"}
    body: dict[str, Any] = {
        **({} if description is None else {"description": description}),
        **({} if status is None else {"status": status}),
        **({} if notes is None else {"notes": notes}),
    }
    if not body:
        return {"error": "No fields to update"}
    result = _cx("PATCH", f"/entities/{entity_id}", body)
    if "error" not in result:
        logger.info("cortex entity_update: %s", entity_id)
    return result


def _op_assertions(
    entity_id: str | None = None,
    confidence: str | None = None,
    limit: int | None = None,
    **_: object,
) -> dict[str, Any]:
    params: dict[str, object] = {"limit": limit or 50}
    if entity_id is not None:
        params["entity_id"] = entity_id
    if confidence is not None:
        params["confidence"] = confidence
    return _cx("GET", f"/assertions?{urlencode(params)}")


_VALID_CONFIDENCE = {"confirmed", "believed", "suspected", "hypothesized"}


def _op_assert(
    entity_id: str | None = None,
    claim: str | None = None,
    confidence: str | None = None,
    evidence: str | None = None,
    evidence_uris: list[str] | str | None = None,
    **_: object,
) -> dict[str, Any]:
    required_fields = {
        "entity_id": entity_id,
        "claim": claim,
        "confidence": confidence,
        "evidence": evidence,
    }
    for field, val in required_fields.items():
        if not val:
            return {"error": f"{field} is required"}
    assert confidence is not None  # for type narrowing after validation
    if confidence not in _VALID_CONFIDENCE:
        return {
            "error": f"Invalid confidence {confidence!r}. "
            f"Must be one of: {sorted(_VALID_CONFIDENCE)}"
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
    result = _cx("POST", "/assertions", body)
    if "error" not in result:
        logger.info("cortex assert: %s — %s (%s)", entity_id, claim[:60], confidence)
        record(
            "mcp.cortex.assertion.seeded", entity_id=entity_id, confidence=confidence
        )
    return result


def _op_deadlines(**_: object) -> dict[str, Any]:
    return _cx("GET", "/deadlines")


def _op_journal_read(limit: int | None = None, **_: object) -> dict[str, Any]:
    return _cx("GET", f"/session-journals?limit={limit or 3}")


def _op_journal_write(
    timestamp: str | None = None,
    agent: str | None = None,
    summary: str | None = None,
    domains: list[str] | None = None,
    decisions: list[str] | None = None,
    open_items: list[str] | None = None,
    file_path: str | None = None,
    **_: object,
) -> dict[str, Any]:
    required_fields = {"timestamp": timestamp, "agent": agent, "summary": summary}
    for field, val in required_fields.items():
        if not val:
            return {"error": f"{field} is required"}
    body: dict[str, Any] = {
        "timestamp": timestamp,
        "agent": agent,
        "summary": summary,
        **({} if domains is None else {"domains": domains}),
        **({} if decisions is None else {"decisions": decisions}),
        **({} if open_items is None else {"open_items": open_items}),
        **({} if file_path is None else {"file_path": file_path}),
    }
    result = _cx("POST", "/session-journals", body)
    if "error" not in result:
        logger.info("cortex journal_write: %s agent=%s", timestamp, agent)
    return result


def _op_review_queue(limit: int | None = None, **_: object) -> dict[str, Any]:
    lim = limit or 30
    staging = _cx("GET", f"/staging?status=pending&limit={lim}")
    assertions = _cx("GET", f"/assertions?superseded=false&limit={lim}")

    staging_items = staging.get("items", []) if not staging.get("error") else []
    assertion_items = (
        [
            a
            for a in assertions.get("items", [])
            if a.get("confidence") in ("suspected", "hypothesized")
            and not a.get("human_reviewed")
        ]
        if not assertions.get("error")
        else []
    )
    return {
        "staging": staging_items,
        "assertions": assertion_items,
        "total": len(staging_items) + len(assertion_items),
    }


def _op_stage(
    proposals: list[dict[str, Any]] | None = None, **_: object
) -> dict[str, Any]:
    if not proposals:
        return {"error": "proposals is required"}
    result = _cx("POST", "/staging/batch", {"proposals": proposals})
    if "error" not in result:
        count = len(result.get("items", []))
        logger.info("cortex stage: %d proposals staged", count)
        record("mcp.cortex.staging.batch", count=count)
    return result


def _op_staging_approve(
    staging_id: int | None = None,
    reviewer: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if staging_id is None:
        return {"error": "staging_id is required"}
    result = _cx(
        "POST", f"/staging/{staging_id}/approve", {"reviewer": reviewer or "web"}
    )
    if "error" not in result:
        logger.info(
            "cortex staging_approve: %d -> %s", staging_id, result.get("resolved_to")
        )
        record("mcp.cortex.staging.approved", staging_id=staging_id)
    return result


# ── op dispatch table ────────────────────────────────────────────────────

_OPS: dict[str, Any] = {
    "entities": _op_entities,
    "entity_get": _op_entity_get,
    "entity_create": _op_entity_create,
    "entity_update": _op_entity_update,
    "assertions": _op_assertions,
    "assert": _op_assert,
    "deadlines": _op_deadlines,
    "journal_read": _op_journal_read,
    "journal_write": _op_journal_write,
    "review_queue": _op_review_queue,
    "stage": _op_stage,
    "staging_approve": _op_staging_approve,
}


# ── registration ─────────────────────────────────────────────────────────


def register_cortex_tools(mcp: FastMCP) -> None:
    """Register the dispatch-style cortex tool on the MCP server instance."""

    @mcp.tool()
    def cortex(tool: str, arguments: str = "{}") -> Any:
        """Cortex knowledge system — dispatch by tool name.

        Available tools:
          entities(type?, limit?) — list entities
          entity_get(entity_id) — get entity with assertions
          entity_create(id, type, name, description?, status?, notes?, aliases?, attributes?, source_uri?)
              Create a new entity. Returns 409 if the entity already exists.
              status: confirmed (default) / provisional / merged / deprecated
          entity_update(entity_id, description?, status?, notes?) — update entity
          assertions(entity_id?, confidence?, limit?) — list assertions
          assert(entity_id, claim, confidence, evidence, evidence_uris?) — create assertion
              Direct write with no review gate. Use for session observations,
              confirmed decisions, and real-time notes made during the current
              conversation.
              confidence: confirmed / believed / suspected / hypothesized
              evidence_uris: agent-bus:034, session:web-2026-03-16, doc:notes/...
          deadlines() — Retrieve a list of legal deadlines
          journal_read(limit?) — read recent session journals
          journal_write(timestamp, agent, summary, domains?, decisions?, open_items?, file_path?)
          review_queue(limit?) — pending staging + low-confidence assertions
          stage(proposals) — batch-stage proposals for review
              Use for bulk ingestion, extraction output, or uncertain claims that
              should land in the human review queue before becoming assertions.
          staging_approve(staging_id, reviewer?) — approve staging proposal

        Example:
            cortex(tool="entities", arguments='{"type": "person", "limit": 20}')
            cortex(tool="entity_create", arguments='{"id": "goal:foo", "type": "goal", "name": "Foo", "description": "..."}')
            cortex(tool="assert", arguments='{"entity_id": "person:foo", "claim": "...", "confidence": "confirmed", "evidence": "..."}')

        Args:
            tool: Name of the cortex operation to invoke.
            arguments: JSON string of operation arguments (default "{}").

        Returns:
            Operation-specific result dict, or {"error": "<message>"}.
        """
        import json as _json

        handler = _OPS.get(tool)
        if handler is None:
            return {"error": f"Unknown cortex tool {tool!r}. Available: {sorted(_OPS)}"}
        parsed = _json.loads(arguments)
        record("mcp.cortex.dispatch", tool=tool)
        return handler(**parsed)
