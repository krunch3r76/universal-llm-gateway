"""Cortex dispatch tool — cortex(tool=..., arguments=...) surface for the Cortex knowledge system.

Routes all Cortex CRUD operations through cortex-api via the local_api relay.
Core handlers live here; extra dispatch ops (assertion lifecycle, relationships,
stats, surface forms) live in cortex_dispatch_ops.py and are imported into _OPS.
Named tools (boot, chunks, staging extras) register via cortex_named_tools.py.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from mcp_events import record

from ._cortex_relay import _cx
from .cortex_dispatch_ops import (
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
_DEFAULT_USER_ENTITY = os.getenv("CORTEX_DEFAULT_USER_ENTITY", "")
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
    reasoning_summary: str | None = None,
    # v3: Kumiho grounding
    prospective_summary: str | None = None,
    events_json: str | None = None,
    artifact_uri: str | None = None,
    artifact_storage: str | None = None,
    # C2: explicit contradiction bypass
    force: bool = False,
    supersedes_id: int | None = None,
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
        ("reasoning_summary", reasoning_summary),
        # v3: Kumiho grounding
        ("prospective_summary", prospective_summary),
        ("events_json", events_json),
        ("artifact_uri", artifact_uri),
        ("artifact_storage", artifact_storage),
    ]:
        if val is not None:
            body[key] = val
    if force:
        body["force"] = True
    if supersedes_id is not None:
        body["supersedes_id"] = supersedes_id
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


def _op_observe(
    entity_id: str | None = None,
    claim: str | None = None,
    confidence: str = "believed",
    agent: str | None = None,
    evidence: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Lightweight observation — 1 required field (claim), sensible defaults.

    Use for inline observations about entities during normal work.
    Defaults: entity_id from CORTEX_DEFAULT_USER_ENTITY env var, confidence=believed,
    derivation_type=agent_observation, seeded_by={agent}, observed_at=now.
    """
    if not entity_id:
        entity_id = _DEFAULT_USER_ENTITY
    if not entity_id:
        return {
            "error": "entity_id is required (set CORTEX_DEFAULT_USER_ENTITY env var for a default)"
        }
    if not claim:
        return {"error": "claim is required"}
    if confidence not in _VALID_CONFIDENCE:
        return {
            "error": f"Invalid confidence {confidence!r}. "
            f"Must be one of: {sorted(_VALID_CONFIDENCE)}"
        }
    body: dict[str, Any] = {
        "entity_id": entity_id,
        "claim": claim,
        "confidence": confidence,
        "evidence": evidence or "Agent observation during session",
        "derivation_type": "agent_observation",
        "observed_at": datetime.now(UTC).isoformat(),
        "confidence_score": 0.8 if confidence == "believed" else 0.6,
    }
    if agent:
        body["seeded_by"] = agent
    result = _cx("POST", "/assertions", body)
    if "error" not in result:
        logger.info(
            "cortex observe: %s — %s (%s, by %s)",
            entity_id,
            claim[:60],
            confidence,
            agent or "unknown",
        )
        record(
            "mcp.cortex.observation.seeded",
            entity_id=entity_id,
            confidence=confidence,
            agent=agent,
        )
    return result


def _op_friction(
    service: str | None = None,
    category: str | None = None,
    note: str | None = None,
    suggestion: str | None = None,
    agent: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Log a friction event — when tools, schema, or boot context didn't work as expected.

    Categories: tool_mismatch, schema_gap, boot_drift, lesson_gap,
    lesson_conflict, stale_context, tool_absent.
    """
    if not service:
        return {"error": "service is required (e.g. 'mcp-server', 'cortex-api')"}
    if not note:
        return {"error": "note is required — describe what went wrong"}
    valid_categories = {
        "tool_mismatch",
        "schema_gap",
        "boot_drift",
        "lesson_gap",
        "lesson_conflict",
        "stale_context",
        "tool_absent",
    }
    if category and category not in valid_categories:
        return {
            "error": f"Invalid category {category!r}. Must be one of: {sorted(valid_categories)}"
        }
    claim = f"[{category or 'unclassified'}] {note}"
    if suggestion:
        claim += f" — Suggestion: {suggestion}"
    body: dict[str, Any] = {
        "entity_id": f"service:{service}",
        "claim": claim,
        "confidence": "hypothesized",
        "evidence": f"Friction observed by {agent or 'unknown'} during session",
        "derivation_type": "agent_observation",
        "observed_at": datetime.now(UTC).isoformat(),
        "confidence_score": 0.5,
    }
    if agent:
        body["seeded_by"] = agent
    result = _cx("POST", "/assertions", body)
    if "error" not in result:
        logger.info("cortex friction: %s/%s — %s", service, category, note[:60])
        record(
            "mcp.cortex.friction.logged",
            service=service,
            category=category or "unclassified",
            agent=agent,
        )
    return result


def _op_deadlines(**_: object) -> dict[str, Any]:
    return _cx("GET", "/deadlines")


def _op_journal_read(limit: int | None = None, **_: object) -> dict[str, Any]:
    return _cx("GET", f"/session-journals?limit={limit or 3}")


def _derive_session_id_local(agent: str, timestamp: str) -> str:
    """Derive a session ID from agent + timestamp (mirrors cortex-api logic)."""
    import re

    match = re.search(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):?(\d{2})", timestamp)
    if match:
        year, mon, day, hour, minute = match.groups()
        return f"{agent}-{year}-{mon}-{day}-{hour}{minute}"
    now = datetime.now(UTC).strftime("%Y-%m-%d-%H%M")
    return f"{agent}-{now}"


def _op_journal_write(
    timestamp: str | None = None,
    agent: str | None = None,
    summary: str | None = None,
    domains: list[str] | None = None,
    decisions: list[str] | None = None,
    open_items: list[str] | None = None,
    entity_ids: list[str] | None = None,
    file_path: str | None = None,
    session_id: str | None = None,
    prior_session_id: str | None = None,
    markdown_content: str | None = None,
    **_: object,
) -> dict[str, Any]:
    required_fields = {"timestamp": timestamp, "agent": agent, "summary": summary}
    for field, val in required_fields.items():
        if not val:
            return {"error": f"{field} is required"}
    assert agent is not None and timestamp is not None

    derived_id = session_id or _derive_session_id_local(agent, timestamp)

    if markdown_content is not None:
        journal_path = _FILES_ROOT / "notes" / "system" / "journal" / f"{derived_id}.md"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_text(markdown_content, encoding="utf-8")
        logger.info("journal_write: wrote markdown to %s", journal_path)

    body: dict[str, Any] = {
        "timestamp": timestamp,
        "agent": agent,
        "summary": summary,
        **({} if domains is None else {"domains": domains}),
        **({} if decisions is None else {"decisions": decisions}),
        **({} if open_items is None else {"open_items": open_items}),
        **({} if entity_ids is None else {"entity_ids": entity_ids}),
        **({} if file_path is None else {"file_path": file_path}),
        **({} if session_id is None else {"session_id": session_id}),
        **({} if prior_session_id is None else {"prior_session_id": prior_session_id}),
    }
    result = _cx("POST", "/session-journals", body)
    if "error" not in result:
        transcript_entity_id = result.get("transcript_entity_id", "")
        logger.info(
            "cortex journal_write: %s agent=%s transcript=%s",
            timestamp,
            agent,
            transcript_entity_id,
        )
    return result


def _op_edge_create(
    session_id: str | None = None,
    agent: str | None = None,
    from_node: str | None = None,
    to_node: str | None = None,
    edge_type: str | None = None,
    strength: float | None = None,
    edge_source: str | None = None,
    context: str | None = None,
    prompt: str | None = None,
    seeded_by: str | None = None,
    metadata: str | None = None,
    **_: object,
) -> dict[str, Any]:
    required = {
        "session_id": session_id,
        "agent": agent,
        "from_node": from_node,
        "to_node": to_node,
        "edge_type": edge_type,
    }
    for field, val in required.items():
        if not val:
            return {"error": f"{field} is required"}
    body: dict[str, Any] = {
        "session_id": session_id,
        "agent": agent,
        "from_node": from_node,
        "to_node": to_node,
        "edge_type": edge_type,
    }
    for key, val in [
        ("strength", strength),
        ("edge_source", edge_source),
        ("context", context),
        ("prompt", prompt),
        ("seeded_by", seeded_by),
        ("metadata", metadata),
    ]:
        if val is not None:
            body[key] = val
    result = _cx("POST", "/edges", body)
    if "error" not in result:
        record(
            "mcp.cortex.edge.created",
            session_id=session_id,
            edge_type=edge_type,
            from_node=from_node,
            to_node=to_node,
        )
    return result


def _op_edges(
    from_node: str | None = None,
    to_node: str | None = None,
    edge_type: str | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    include_retired: bool | None = None,
    limit: int | None = None,
    **_: object,
) -> dict[str, Any]:
    params: dict[str, object] = {"limit": limit or 50}
    if from_node is not None:
        params["from_node"] = from_node
    if to_node is not None:
        params["to_node"] = to_node
    if edge_type is not None:
        params["edge_type"] = edge_type
    if agent is not None:
        params["agent"] = agent
    if session_id is not None:
        params["session_id"] = session_id
    if include_retired is not None:
        params["include_retired"] = str(include_retired).lower()
    return _cx("GET", f"/edges?{urlencode(params)}")


def _op_edge_traverse(
    node: str | None = None,
    hops: int | None = None,
    edge_type: str | None = None,
    min_strength: float | None = None,
    **_: object,
) -> dict[str, Any]:
    if not node:
        return {"error": "node is required"}
    params: dict[str, object] = {"node": node}
    if hops is not None:
        params["hops"] = hops
    if edge_type is not None:
        params["edge_type"] = edge_type
    if min_strength is not None:
        params["min_strength"] = min_strength
    return _cx("GET", f"/edges/traverse?{urlencode(params)}")


def _op_edge_retire(
    edge_id: int | None = None,
    valid_until: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if edge_id is None:
        return {"error": "edge_id is required"}
    body: dict[str, Any] = {}
    if valid_until is not None:
        body["valid_until"] = valid_until
    return _cx("PATCH", f"/edges/{edge_id}/retire", body)


def _op_edge_types(**_: object) -> Any:
    return _cx("GET", "/edges/types")


def _op_ingest_document(
    source_uri: str | None = None,
    content: str | None = None,
    observer: str = "web",
    source_date: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if not source_uri:
        return {"error": "source_uri is required"}
    if not content:
        return {"error": "content is required"}
    body: dict[str, Any] = {
        "source_uri": source_uri,
        "content": content,
        "observer": observer,
    }
    if source_date is not None:
        body["source_date"] = source_date
    result = _cx("POST", "/ingest-document", body)
    if "error" not in result:
        chunk_count = result.get("chunk_count", 0)
        logger.info("cortex ingest_document: %s — %d chunks", source_uri, chunk_count)
        record(
            "mcp.cortex.ingest_document",
            source_uri=source_uri,
            chunk_count=chunk_count,
        )
    return result


def _op_assert_from_chunk(
    chunk_id: int | None = None,
    entity_id: str | None = None,
    claim: str | None = None,
    confidence: str | None = None,
    evidence: str | None = None,
    evidence_uris: list[str] | str | None = None,
    derivation_type: str | None = None,
    confidence_score: float | None = None,
    observed_at: str | None = None,
    valid_from: str | None = None,
    reasoning_summary: str | None = None,
    resolution_status: str | None = None,
    seeded_by: str | None = None,
    **_: object,
) -> dict[str, Any]:
    required = {
        "chunk_id": chunk_id,
        "entity_id": entity_id,
        "claim": claim,
        "confidence": confidence,
        "evidence": evidence,
    }
    for field, val in required.items():
        if not val and val != 0:
            return {"error": f"{field} is required"}
    body: dict[str, Any] = {
        "chunk_id": chunk_id,
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
        ("derivation_type", derivation_type),
        ("confidence_score", confidence_score),
        ("observed_at", observed_at),
        ("valid_from", valid_from),
        ("reasoning_summary", reasoning_summary),
        ("resolution_status", resolution_status),
        ("seeded_by", seeded_by),
    ]:
        if val is not None:
            body[key] = val
    result = _cx("POST", "/assert-from-chunk", body)
    if "error" not in result:
        logger.info(
            "cortex assert_from_chunk: chunk=%s entity=%s — %s",
            chunk_id,
            entity_id,
            str(claim)[:60],
        )
        record(
            "mcp.cortex.assert_from_chunk",
            chunk_id=chunk_id,
            entity_id=entity_id,
        )
    return result


def _op_search(
    query: str | None = None,
    limit: int | None = None,
    superseded: bool | None = None,
    entity_type: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Fulltext search over assertions (claim + prospective summary + events).

    Use when looking for assertions by semantic content — especially terms that
    appear only in enrichment columns (prospective_summary, events_json), not
    the original claim text.  Prefer over ``assertions`` list when you have a
    natural-language query rather than exact entity_id / confidence filters.
    """
    if not query:
        return {"error": "query is required"}
    params: dict[str, object] = {"q": query, "limit": limit or 20}
    if superseded is not None:
        params["superseded"] = str(superseded).lower()
    if entity_type is not None:
        params["entity_type"] = entity_type
    return _cx("GET", f"/assertions/search?{urlencode(params)}")


def _op_resolve(
    uri: str | None = None, tag: str | None = None, **_: object
) -> dict[str, Any]:
    """Resolve a cortex:// URI to entity + optional assertion data.

    When *tag* is provided, resolve to the assertion pointed to by that tag
    instead of the latest non-superseded assertion.
    """
    if not uri:
        return {"error": "uri is required (e.g. cortex://decision/rag-phased-rollout)"}
    qs = f"uri={uri}"
    if tag is not None:
        qs += f"&tag={tag}"
    return _cx("GET", f"/resolve?{qs}")


def _op_tag_assign(
    tag_name: str | None = None,
    entity_id: str | None = None,
    assertion_id: int | None = None,
    agent: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Assign or move a named tag pointer to a specific assertion on an entity.

    Upsert: if the tag already exists for this entity, the pointer moves.
    Use for named states: 'approved', 'initial', 'disputed', 'v1'.
    """
    for field, val in [
        ("tag_name", tag_name),
        ("entity_id", entity_id),
        ("assertion_id", assertion_id),
        ("agent", agent),
    ]:
        if not val and val != 0:
            return {"error": f"{field} is required"}
    body = {
        "tag_name": tag_name,
        "entity_id": entity_id,
        "assertion_id": assertion_id,
        "assigned_by": agent,
    }
    result = _cx("PUT", "/tags", body)
    if "error" not in result:
        logger.info(
            "cortex tag_assign: %s → assertion %s on %s",
            tag_name,
            assertion_id,
            entity_id,
        )
        record(
            "mcp.cortex.tag.assigned",
            tag_name=tag_name,
            entity_id=entity_id,
            assertion_id=assertion_id,
        )
    return result


def _op_tag_list(entity_id: str | None = None, **_: object) -> dict[str, Any]:
    """List all tag assignments for an entity."""
    if not entity_id:
        return {"error": "entity_id is required"}
    return _cx("GET", f"/tags?entity_id={entity_id}")


def _op_tag_resolve(
    tag_name: str | None = None,
    entity_id: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Resolve a tag to the assertion it points at.

    Shorthand for resolve(uri=cortex://TYPE/SLUG, tag=TAG).
    """
    if not tag_name:
        return {"error": "tag_name is required"}
    if not entity_id:
        return {"error": "entity_id is required"}
    parts = entity_id.split(":", 1)
    if len(parts) != 2:
        return {
            "error": f"Invalid entity_id format: {entity_id!r} (expected TYPE:SLUG)"
        }
    uri = f"cortex://{parts[0]}/{parts[1]}"
    return _cx("GET", f"/resolve?uri={uri}&tag={tag_name}")


def _op_impact(
    entity_id: str | None = None,
    depth: int | None = None,
    **_: object,
) -> dict[str, Any]:
    """Transitive impact analysis — BFS over dependency edges from an entity.

    Surfaces all downstream entities whose beliefs depend on the seed entity.
    Use after superseding an assertion to understand revision cascade scope.
    """
    if not entity_id:
        return {"error": "entity_id is required"}
    params: dict[str, object] = {"entity_id": entity_id}
    if depth is not None:
        params["depth"] = depth
    return _cx("GET", f"/edges/impact?{urlencode(params)}")


def _op_activate(
    entity_ids: list[str] | None = None,
    depth: int | None = None,
    max_results: int | None = None,
    exclude_ids: list[int] | None = None,
    suppress_hubs: bool | None = None,
    decay_factor: float | None = None,
    **_: object,
) -> dict[str, Any]:
    """Spreading activation — walk the graph from seed entities to find related assertions.

    Call after hybrid search to pull in structurally connected assertions the
    query wouldn't find directly.  Pass entity_ids from search results, and
    optionally exclude_ids (assertion IDs already retrieved).
    """
    if not entity_ids:
        return {"error": "entity_ids is required (list of seed entity IDs)"}
    params: dict[str, object] = {"entity_ids": ",".join(entity_ids)}
    if depth is not None:
        params["depth"] = depth
    if max_results is not None:
        params["max_results"] = max_results
    if exclude_ids:
        params["exclude_ids"] = ",".join(str(i) for i in exclude_ids)
    if suppress_hubs is not None:
        params["suppress_hubs"] = str(suppress_hubs).lower()
    if decay_factor is not None:
        params["decay_factor"] = decay_factor
    from urllib.parse import urlencode as _urlencode

    return _cx("GET", f"/assertions/activate?{_urlencode(params)}")


def _op_analyze_impact(
    entity_id: str | None = None,
    claim: str | None = None,
    confidence: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Semantic impact analysis — preview which assertions a proposed claim would affect.

    Uses entity-scoped hybrid search (FTS5 + vector) to find assertions that
    may need revision. Returns touched_assertions, likely_supersedes IDs,
    implicated_entities, and an overall impact_score. Call before assert/supersede
    to understand revision scope.
    """
    if not entity_id:
        return {"error": "entity_id is required"}
    if not claim:
        return {"error": "claim is required"}
    if confidence is not None and confidence not in _VALID_CONFIDENCE:
        return {
            "error": f"Invalid confidence {confidence!r}. "
            f"Must be one of: {sorted(_VALID_CONFIDENCE)}"
        }
    body: dict[str, Any] = {"entity_id": entity_id, "claim": claim}
    if confidence is not None:
        body["confidence"] = confidence
    return _cx("POST", "/assertions/analyze-impact", body)


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
    "observe": _op_observe,
    "friction": _op_friction,
    "assertion_update": _op_assertion_update,
    "supersede": _op_supersede,
    "ingest_document": _op_ingest_document,
    "assert_from_chunk": _op_assert_from_chunk,
    "relationships": _op_relationships,
    "relationship_create": _op_relationship_create,
    "stats": _op_stats,
    "surface_forms": _op_surface_forms,
    "deadlines": _op_deadlines,
    "journal_read": _op_journal_read,
    "journal_write": _op_journal_write,
    "review_queue": _op_review_queue,
    "edge_create": _op_edge_create,
    "edges": _op_edges,
    "edge_traverse": _op_edge_traverse,
    "edge_retire": _op_edge_retire,
    "edge_types": _op_edge_types,
    "impact": _op_impact,
    "activate": _op_activate,
    "resolve": _op_resolve,
    "search": _op_search,
    "analyze_impact": _op_analyze_impact,
    "tag_assign": _op_tag_assign,
    "tag_list": _op_tag_list,
    "tag_resolve": _op_tag_resolve,
}


# ── registration ─────────────────────────────────────────────────────────


_CORTEX_FORMAT_HINT = (
    "arguments must be a JSON string, not a bare object. "
    'Example: cortex(tool="entity_get", arguments=\'{"entity_id": "service:mcp-server"}\')'
)

_CORTEX_HALLUCINATED_TOOLS: dict[str, str] = {
    "search_assertions": "search",
    "search_entities": "entities",
    "get_entity": "entity_get",
    "entity_search": "search",
    "assert_entity": "assert",
    "create_entity": "entity_create",
    "update_entity": "entity_update",
}


def _parse_cortex_arguments(arguments: object, tool: str) -> dict[str, Any] | None:
    """Return parsed arguments dict, or None with side-effect logging on failure."""
    import json as _json

    if isinstance(arguments, dict):
        logger.warning(
            "cortex %r: arguments passed as dict (object), not a JSON string — "
            "format violation. Accepting for compatibility but callers should fix.",
            tool,
        )
        return arguments
    if isinstance(arguments, str):
        try:
            result = _json.loads(arguments)
        except _json.JSONDecodeError as exc:
            logger.warning("cortex %r: arguments JSON parse failed: %s", tool, exc)
            return None
        if not isinstance(result, dict):
            return None
        return result
    return None


def register_cortex_tools(mcp: FastMCP) -> None:
    """Register the dispatch-style cortex tool on the MCP server instance."""

    @mcp.tool(title="Cortex Knowledge Graph")
    def cortex(tool: str, arguments: str = "{}") -> Any:
        """Cortex knowledge system — entities, assertions, relationships, edges, journals.

        tool: operation name (see table below)
        arguments: JSON string with operation arguments

        Operations:
          entities          (type?, limit?)                          — list entities
          entity_get        (entity_id)                             — get entity with assertions + relationships
          entity_create     (id, type, name, description?, status?, notes?, aliases?, attributes?, source_uri?) — create entity
          entity_update     (entity_id, name?, description?, status?, notes?, aliases?, attributes?)  — update entity
          assertions        (entity_id?, confidence?, review_status?, superseded?, limit?) — list assertions
          assert            (entity_id, claim, confidence, evidence, evidence_uris?, seeded_by?, derivation_type?, force?, supersedes_id?) — write assertion
          assertion_update  (assertion_id, superseded_by?, valid_until?, confidence?, review_status?) — update assertion
          supersede         (old_assertion_id, entity_id, claim, confidence, evidence, session_id, agent) — atomic close+create
          relationships     (entity_id?, type_id?, limit?)          — list with names, strength
          relationship_create (source_id, target_id, type_id, role?, strength?, evidence?) — create relationship
          stats             ()                                       — dashboard counts
          surface_forms     (entity_id?, mention?, mention_type?, limit?) — resolution cache
          journal_read      (limit?)                                 — recent session journals
          journal_write     (timestamp, agent, summary, domains?, decisions?, open_items?, entity_ids?, session_id?, prior_session_id?, markdown_content?) — write journal; auto-creates transcript entity + continues edge; markdown_content writes file server-side
          review_queue      (limit?)                                 — provisional entities + flagged assertions
          edge_create       (session_id, agent, from_node, to_node, edge_type, strength?, context?) — seed reasoning connection
          edges             (from_node?, to_node?, edge_type?, agent?, session_id?, limit?) — query edges
          edge_traverse     (node, hops?, edge_type?, min_strength?) — graph traversal (1-2 hops)
          edge_retire       (edge_id, valid_until?)                  — retire an edge
          edge_types        ()                                        — list registered edge types
          search            (query, limit?, superseded?, entity_type?) — FTS5 fulltext search over assertions
          analyze_impact    (entity_id, claim, confidence?)            — semantic pre-write impact analysis (C1)

        confidence values: confirmed / believed / suspected / hypothesized
        review_status values: committed / flagged / staged / rejected

        Example:
          cortex(tool="entities", arguments='{"type": "todo", "limit": 20}')
          cortex(tool="assert", arguments='{"entity_id": "person:foo", "claim": "...", "confidence": "confirmed", "evidence": "..."}')
        """
        handler = _OPS.get(tool)
        if handler is None:
            suggestion = _CORTEX_HALLUCINATED_TOOLS.get(tool)
            hint = f"Did you mean {suggestion!r}?" if suggestion else None
            return {
                "error": f"Unknown cortex tool {tool!r}. Available: {sorted(_OPS)}",
                **({"hint": hint} if hint else {}),
                "format_example": (
                    'cortex(tool="entity_get", arguments=\'{"entity_id": "type:slug"}\')'
                ),
            }

        parsed = _parse_cortex_arguments(arguments, tool)
        if parsed is None:
            return {
                "error": _CORTEX_FORMAT_HINT,
                "format_example": (
                    f'cortex(tool="{tool}", arguments=\'{{"entity_id": "type:slug"}}\')'
                ),
            }

        record("mcp.cortex.dispatch", tool=tool)
        return handler(**parsed)
