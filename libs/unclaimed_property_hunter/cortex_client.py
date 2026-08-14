"""UDS client for cortex-api `/dispatch` — entity, assert, and list ops.

The hunter persists provenance here because vortex MCP is not required at
run time. Callers must treat a response with `error` as a failed write.
"""

from __future__ import annotations

from typing import Any

import httpx

CORTEX_API_SOCK = "/tmp/universal-protocol/cortex-api.sock"


def dispatch(tool: str, arguments: dict[str, Any], *, timeout_s: float = 20.0) -> dict[str, Any]:
    """POST one cortex op and return the JSON object (including error dicts)."""
    transport = httpx.HTTPTransport(uds=CORTEX_API_SOCK)
    with httpx.Client(transport=transport, timeout=timeout_s) as client:
        response = client.post(
            "http://localhost/dispatch",
            json={"tool": tool, "arguments": arguments},
        )
    if response.status_code == 409:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {"body": payload}
        payload.setdefault("error", "409 Conflict")
        payload["status_code"] = 409
        return payload
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"cortex {tool} returned non-object: {payload!r}")
    return payload


def create_entity(**arguments: Any) -> dict[str, Any]:
    """Create a Cortex entity and return the payload, including 409 conflicts.

    Callers treat an `error` key as a failed write unless it is a duplicate id.
    """
    return dispatch("entity_create", arguments)


def get_entity(entity_id: str) -> dict[str, Any]:
    """Fetch one Cortex entity card by id for idempotent create follow-up."""
    return dispatch("entity_get", {"entity_id": entity_id, "intent": "card"})


def assert_claim(**arguments: Any) -> dict[str, Any]:
    """Write one assertion; caller supplies evidence_uris to the raw payload."""
    return dispatch("assert", arguments)


def list_entities(*, entity_type: str, query: str, limit: int = 50) -> dict[str, Any]:
    """List Cortex entities of one type whose id or name matches `query`."""
    return dispatch(
        "entities",
        {"type": entity_type, "query": query, "limit": limit},
    )
