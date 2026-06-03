"""cortex-api dispatch helpers for the todo_close pipeline.

Thin async wrappers around POST /dispatch — each returns a dict that always
has either a valid response payload or an ``{"error": ...}`` key; callers
check for the ``"error"`` key to detect failures.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_REL_TYPE = "references"
_DEFAULT_EDGE_TYPE = "depends_on"
_DEPENDS_ON_CONTEXT = "Dependency closed in same cycle as todo."


def missing_keys(payload: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    return [k for k in keys if not payload.get(k)]


def default_evidence(todo_id: str, agent: str | None, session_id: str | None) -> str:
    parts = [f"Closure of {todo_id}"]
    if agent:
        parts.append(f"by {agent}")
    if session_id:
        parts.append(f"in session {session_id}")
    parts.append(f"at {datetime.now(UTC).isoformat()}")
    return " ".join(parts) + "."


async def dispatch(client: Any, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """POST /dispatch and normalize the response into a dict.

    cortex-api `/dispatch` returns either a JSON object (success or
    structured `{"error": "..."}`) or a non-200 with a body. We surface
    everything as a dict; the caller decides whether `"error"` is present.
    """
    try:
        resp = await client.post(
            "/dispatch", json={"tool": tool, "arguments": arguments}
        )
    except Exception as exc:
        logger.warning("cortex dispatch %s failed: %s", tool, exc)
        return {"error": f"transport_error: {exc}"}
    if resp.status_code >= 400:
        try:
            body = resp.json()
            if isinstance(body, dict):
                return body if "error" in body else {"error": json.dumps(body)[:300]}
        except Exception as exc:
            logger.warning("could not parse error response body: %s", exc)
        return {"error": f"http_{resp.status_code}: {resp.text[:300]}"}
    try:
        data = resp.json()
    except Exception as exc:
        logger.warning("cortex dispatch %s returned invalid JSON: %s", tool, exc)
        return {"error": f"invalid_json_response: {exc}"}
    return data if isinstance(data, dict) else {"error": "non_object_response"}


async def do_assert(
    client: Any,
    *,
    todo_id: str,
    summary: str,
    evidence: str,
    evidence_uris: list[str] | None,
    reasoning_summary: str | None,
    agent: str | None,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "entity_id": todo_id,
        "claim": summary,
        "confidence": "confirmed",
        "evidence": evidence,
        "derivation_type": "agent_observation",
        "confidence_score": 0.8,
    }
    if evidence_uris:
        args["evidence_uris"] = list(evidence_uris)
    if reasoning_summary:
        args["reasoning_summary"] = reasoning_summary
    if agent:
        args["seeded_by"] = agent
    return await dispatch(client, "assert", args)


async def do_relationship(
    client: Any,
    *,
    todo_id: str,
    item: dict[str, Any],
    agent: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    target = item.get("target")
    if not target:
        return {"error": "references[].target is required"}
    args: dict[str, Any] = {
        "source_id": todo_id,
        "target_id": target,
        "type_id": item.get("type_id") or _DEFAULT_REL_TYPE,
    }
    for k in ("role", "evidence", "strength"):
        if item.get(k) is not None:
            args[k] = item[k]
    if agent:
        args["agent"] = agent
    if session_id:
        args["session_id"] = session_id
    return await dispatch(client, "relationship_create", args)


async def do_edge(
    client: Any,
    *,
    from_node: str,
    to_node: str,
    edge_type: str,
    context_text: str | None,
    strength: float | None,
    agent: str,
    session_id: str,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "session_id": session_id,
        "agent": agent,
        "from_node": from_node,
        "to_node": to_node,
        "edge_type": edge_type,
    }
    if context_text:
        args["context"] = context_text
    if strength is not None:
        args["strength"] = strength
    return await dispatch(client, "edge_create", args)


async def do_sidecar(
    client: Any,
    *,
    todo_id: str,
    summary: str,
    evidence: str | None,
    reasoning_summary: str | None,
    references: list[dict[str, Any]] | None,
    agent: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    args: dict[str, Any] = {"todo_id": todo_id, "summary": summary}
    if evidence:
        args["evidence"] = evidence
    if reasoning_summary:
        args["reasoning_summary"] = reasoning_summary
    if references:
        args["references"] = references
    if agent:
        args["agent"] = agent
    if session_id:
        args["session_id"] = session_id
    return await dispatch(client, "todo_close_sidecar", args)


async def do_workflow_update(client: Any, todo_id: str) -> dict[str, Any]:
    return await dispatch(
        client,
        "entity_update",
        {"entity_id": todo_id, "workflow_state": "done"},
    )


def extract_id(result: dict[str, Any], *keys: str) -> Any:
    """Pull an id out of a dispatch result, tolerant of nested shapes."""
    for k in keys:
        if k in result:
            return result[k]
    inner = result.get("data") or result.get("item")
    if isinstance(inner, dict):
        for k in keys:
            if k in inner:
                return inner[k]
    return None
