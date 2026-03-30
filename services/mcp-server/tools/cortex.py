"""Cortex dispatch tool — cortex(tool=..., arguments=...) surface for the Cortex knowledge system.

Routes all Cortex CRUD operations through cortex-api via the local_api relay.
Core handlers live here; v2.1 handlers (assertion lifecycle, relationships,
stats, surface forms) live in cortex_v21.py and are imported into _OPS.
Only cortex_boot remains as a standalone tool (in cortex_v2.py).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from mcp_events import record

from ._cortex_relay import _cx
from .cortex_v21 import (
    _op_assertion_update,
    _op_relationship_create,
    _op_relationships,
    _op_stats,
    _op_supersede,
    _op_surface_forms,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


_FILES_ROOT = Path("/data/files")
_ENTITY_MUTABLE = frozenset(
    {
        "name",
        "aliases",
        "attributes",
        "notes",
        "source_uri",
        "description",
        "status",
        "content_hash",
    }
)


def _compute_content_hash(source_uri: str) -> str | None:
    """SHA-256 of a local file under /data/files. None if not local or missing."""
    local_path = _FILES_ROOT / source_uri
    if not local_path.is_file():
        return None
    h = hashlib.sha256()
    with open(local_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


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
    content_hash: str | None = None,
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
    if source_uri is not None and content_hash is None:
        content_hash = _compute_content_hash(source_uri)
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
        **({} if content_hash is None else {"content_hash": content_hash}),
    }
    result = _cx("POST", "/entities", body)
    if "error" not in result:
        logger.info("cortex entity_create: %s (%s)", id, type)
        record("mcp.cortex.entity.created", entity_id=id, entity_type=type)
    return result


def _op_entity_update(
    entity_id: str | None = None,
    **kwargs: object,
) -> dict[str, Any]:
    if not entity_id:
        return {"error": "entity_id is required"}
    body: dict[str, Any] = {k: v for k, v in kwargs.items() if k in _ENTITY_MUTABLE}
    if (
        "source_uri" in body
        and body["source_uri"] is not None
        and "content_hash" not in body
    ):
        computed = _compute_content_hash(body["source_uri"])
        if computed:
            body["content_hash"] = computed
    if not body:
        return {"error": "No fields to update"}
    result = _cx("PATCH", f"/entities/{entity_id}", body)
    if "error" not in result:
        logger.info("cortex entity_update: %s", entity_id)
    return result


def _op_assertions(
    entity_id: str | None = None,
    confidence: str | None = None,
    review_status: str | None = None,
    superseded: bool | None = None,
    limit: int | None = None,
    **_: object,
) -> dict[str, Any]:
    params: dict[str, object] = {"limit": limit or 50}
    if entity_id is not None:
        params["entity_id"] = entity_id
    if confidence is not None:
        params["confidence"] = confidence
    if review_status is not None:
        params["review_status"] = review_status
    if superseded is not None:
        params["superseded"] = str(superseded).lower()
    return _cx("GET", f"/assertions?{urlencode(params)}")


_VALID_CONFIDENCE = {"confirmed", "believed", "suspected", "hypothesized"}


def _op_assert(
    entity_id: str | None = None,
    claim: str | None = None,
    confidence: str | None = None,
    evidence: str | None = None,
    evidence_uris: list[str] | str | None = None,
    seeded_by: str | None = None,
    derivation_type: str | None = None,
    confidence_score: float | None = None,
    observed_at: str | None = None,
    valid_from: str | None = None,
    chunk_id: int | None = None,
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
    for key, val in [
        ("seeded_by", seeded_by),
        ("derivation_type", derivation_type),
        ("confidence_score", confidence_score),
        ("observed_at", observed_at),
        ("valid_from", valid_from),
        ("chunk_id", chunk_id),
    ]:
        if val is not None:
            body[key] = val
    if derivation_type is None or confidence_score is None:
        logger.warning(
            "cortex assert: missing derivation_type=%s or confidence_score=%s — "
            "these will become mandatory in a future version",
            derivation_type,
            confidence_score,
        )
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
    entity_ids: list[str] | None = None,
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
        **({} if entity_ids is None else {"entity_ids": entity_ids}),
        **({} if file_path is None else {"file_path": file_path}),
    }
    result = _cx("POST", "/session-journals", body)
    if "error" not in result:
        logger.info("cortex journal_write: %s agent=%s", timestamp, agent)
    return result


def _op_review_queue(limit: int | None = None, **_: object) -> dict[str, Any]:
    lim = limit or 30
    flagged_resp = _cx(
        "GET", f"/assertions?review_status=flagged&superseded=false&limit={lim}"
    )
    low_conf_resp = _cx("GET", f"/assertions?superseded=false&limit={lim}")
    entities = _cx("GET", f"/entities?limit={lim}")

    flagged = (
        [
            {**a, "priority": 2, "reason": "flagged"}
            for a in flagged_resp.get("items", [])
        ]
        if not flagged_resp.get("error")
        else []
    )

    low_conf = []
    if not low_conf_resp.get("error"):
        for a in low_conf_resp.get("items", []):
            if a.get("confidence") in ("suspected", "hypothesized"):
                low_conf.append({**a, "priority": 3, "reason": "low_confidence"})

    provisional = []
    thin_descriptions = []
    if not entities.get("error"):
        for e in entities.get("items", []):
            if e.get("status") == "provisional":
                provisional.append({**e, "priority": 1, "reason": "provisional"})
            desc = e.get("description") or ""
            if len(desc) < 50:
                thin_descriptions.append(
                    {**e, "priority": 4, "reason": "thin_description"}
                )

    total = len(flagged) + len(provisional) + len(low_conf) + len(thin_descriptions)
    return {
        "provisional_entities": provisional,
        "flagged_assertions": flagged,
        "low_confidence_assertions": low_conf,
        "thin_descriptions": thin_descriptions,
        "total": total,
    }


# ── op dispatch table ────────────────────────────────────────────────────

_OPS: dict[str, Any] = {
    "entities": _op_entities,
    "entity_get": _op_entity_get,
    "entity_create": _op_entity_create,
    "entity_update": _op_entity_update,
    "assertions": _op_assertions,
    "assert": _op_assert,
    "assertion_update": _op_assertion_update,
    "supersede": _op_supersede,
    "relationships": _op_relationships,
    "relationship_create": _op_relationship_create,
    "stats": _op_stats,
    "surface_forms": _op_surface_forms,
    "deadlines": _op_deadlines,
    "journal_read": _op_journal_read,
    "journal_write": _op_journal_write,
    "review_queue": _op_review_queue,
}


# ── registration ─────────────────────────────────────────────────────────


def register_cortex_tools(mcp: FastMCP) -> None:
    """Register the dispatch-style cortex tool on the MCP server instance."""

    @mcp.tool()
    def cortex(tool: str, arguments: str = "{}") -> Any:
        """Cortex knowledge system — dispatch by tool name.

        Available tools:
          entities(type?, limit?) — list entities
          entity_get(entity_id) — get entity with assertions + relationships
          entity_create(id, type, name, description?, status?, notes?, aliases?, attributes?, source_uri?, content_hash?)
              Create a new entity. Returns 409 if the entity already exists.
              status: confirmed (default) / provisional / merged / deprecated
              content_hash: sha256:<hex> fingerprint. Auto-computed from source_uri
              when it resolves to a local file under /data/files/.
          entity_update(entity_id, name?, description?, status?, notes?, aliases?, attributes?, source_uri?, content_hash?)
              Update mutable entity metadata. Send a field as null to clear it;
              omit a field to leave it untouched. content_hash is auto-computed
              when source_uri is set and resolves to a local file.
          assertions(entity_id?, confidence?, review_status?, superseded?, limit?)
              List assertions. review_status: committed/flagged/staged/rejected
          assert(entity_id, claim, confidence, evidence, evidence_uris?,
                 seeded_by?, derivation_type?, confidence_score?, observed_at?, valid_from?, chunk_id?)
              Direct write with no review gate. Use for session observations,
              confirmed decisions, and real-time notes.
              seeded_by: optional agent/frontier provenance tag for seeded assertions
              confidence: confirmed / believed / suspected / hypothesized
              derivation_type: quotation / compression / inference / other
              confidence_score: 0.0–1.0 numeric confidence
          assertion_update(assertion_id, superseded_by?, valid_until?, confidence?,
              confidence_score?, review_status?, reviewer?, reviewed_at?)
              Update metadata. review_status: committed/flagged/staged/rejected
          supersede(old_assertion_id, entity_id, claim, confidence, evidence,
              evidence_uris?, valid_from?, derivation_type?)
              Atomic: closes old + creates new in one transaction.
          relationships(entity_id?, type_id?, limit?) — list with names, strength
          relationship_create(source_id, target_id, type_id, role?, strength?,
              evidence?, chunk_id?, valid_from?, valid_until?, source_uri?)
          stats() — dashboard counts across all tables
          surface_forms(entity_id?, mention?, mention_type?, limit?) — resolution cache
          deadlines() — legal deadlines
          journal_read(limit?) — recent session journals
          journal_write(timestamp, agent, summary, domains?, decisions?, open_items?, entity_ids?, file_path?)
              entity_ids: JSON array of entity IDs referenced in this session.
              At next boot, cortex_boot injects current state for these entities.
          review_queue(limit?) — provisional entities + flagged assertions +
              low-confidence unreviewed + thin descriptions (prioritized)

        Example:
            cortex(tool="entities", arguments='{"type": "person", "limit": 20}')
            cortex(tool="supersede", arguments='{"old_assertion_id": 4, "entity_id": "person:foo", "claim": "...", "confidence": "confirmed", "evidence": "..."}')
            cortex(tool="relationships", arguments='{"entity_id": "person:kaywan"}')

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
