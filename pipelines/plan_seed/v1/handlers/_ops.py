"""cortex-api dispatch helpers for the plan_seed pipeline.

Thin async wrappers around POST /dispatch — each returns a dict that always
has either a valid response payload or an ``{"error": ...}`` key; callers
check for the ``"error"`` key to detect failures.
"""

from __future__ import annotations

import json
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_DERIVED_FROM = "derived_from"
_DERIVED_CONTEXT = "Plan derived from its source todo (same seed cycle)."


def missing_keys(payload: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    return [k for k in keys if not payload.get(k)]


async def dispatch(client: Any, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """POST /dispatch and normalize the response into a dict."""
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


async def do_entity_create(
    client: Any,
    *,
    entity_id: str,
    entity_type: str,
    name: str,
    description: str | None,
    source_uri: str | None,
    attributes: dict[str, Any] | None,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "id": entity_id,
        "type": entity_type,
        "name": name,
        "status": "confirmed",
        "workflow_state": "open",
    }
    if description:
        args["description"] = description
    if source_uri:
        args["source_uri"] = source_uri
    if attributes:
        args["attributes"] = attributes
    return await dispatch(client, "entity_create", args)


async def do_derived_edge(
    client: Any,
    *,
    plan_id: str,
    todo_id: str,
    agent: str,
    session_id: str,
) -> dict[str, Any]:
    return await dispatch(
        client,
        "edge_create",
        {
            "session_id": session_id,
            "agent": agent,
            "from_node": plan_id,
            "to_node": todo_id,
            "edge_type": _DERIVED_FROM,
            "context": _DERIVED_CONTEXT,
        },
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
