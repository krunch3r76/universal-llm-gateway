"""POST cortex-api ``entity_create`` for substrate entity-mint writes.

Callers are the MCP ``substrate_entity_mint`` verb. The lib is the only
Cortex POST author so a later GIW adapter can import it without crossing
the mcp-server / git_integration_worker domain wall.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

_TIMEOUT = 30.0

# Named params ``_op_entity_create`` consumes (excluding ``**extra`` aliases).
ENTITY_CREATE_REQUIRED = ("id", "type", "name")
ENTITY_CREATE_OPTIONAL = (
    "description",
    "status",
    "workflow_state",
    "notes",
    "aliases",
    "attributes",
    "source_uri",
    "content_hash",
)
ENTITY_CREATE_FORWARD = ENTITY_CREATE_REQUIRED + ENTITY_CREATE_OPTIONAL


def resolve_create_slot(
    *,
    primary: str = "",
    alias: str = "",
    primary_name: str,
    alias_name: str,
) -> tuple[str | None, str | None]:
    """Resolve primary vs alias for one identity slot; error when both differ."""
    primary_text = (primary or "").strip()
    alias_text = (alias or "").strip()
    if primary_text and alias_text and primary_text != alias_text:
        return None, (
            f"substrate_entity_mint: supply {primary_name}= or {alias_name}= "
            "(same slot), not both with different values"
        )
    resolved = primary_text or alias_text
    return resolved or None, None


def mint_entity(
    *,
    id: str,
    type: str,
    name: str,
    description: str | None = None,
    status: str | None = None,
    workflow_state: str | None = None,
    notes: str | None = None,
    aliases: list[str] | str | None = None,
    attributes: dict[str, Any] | str | None = None,
    source_uri: str | None = None,
    content_hash: str | None = None,
    seat: str = "cursor-sdk",
    via_adapter: bool = True,
    surface: str = "code",
) -> dict[str, Any]:
    """Mint an entity via cortex-api ``/dispatch`` tool=entity_create.

    Forwards every dispatch-consumed field verbatim when supplied. Does not
    invent ids, run rich-seed ceremony, or enqueue a bus turn. HTTP 409 on
    exact-slug collision passes through.
    """
    arguments: dict[str, Any] = {
        "id": id.strip(),
        "type": type.strip(),
        "name": name.strip(),
    }
    if description is not None:
        arguments["description"] = description
    if status is not None:
        arguments["status"] = status
    if workflow_state is not None:
        arguments["workflow_state"] = workflow_state
    if notes is not None:
        arguments["notes"] = notes
    if aliases is not None:
        arguments["aliases"] = aliases
    if attributes is not None:
        arguments["attributes"] = attributes
    if source_uri is not None:
        arguments["source_uri"] = source_uri
    if content_hash is not None:
        arguments["content_hash"] = content_hash
    body = {
        "tool": "entity_create",
        "arguments": json.dumps(arguments),
        "surface": surface,
        "via_adapter": via_adapter,
        "seat": seat,
    }
    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=_TIMEOUT) as client:
            response = client.post("/dispatch", json=body)
    except httpx.RequestError as exc:
        return {"error": f"cortex-api connection failed: {exc}", "status_code": None}
    if response.status_code >= 400:
        return {
            "error": f"cortex-api error: HTTP {response.status_code} — {response.text}",
            "status_code": response.status_code,
        }
    try:
        parsed = response.json()
    except ValueError:
        return {
            "error": f"cortex-api returned invalid JSON: {response.text[:200]}",
            "status_code": None,
        }
    if not isinstance(parsed, dict):
        return {"error": f"cortex-api returned {type(parsed).__name__}", "status_code": None}
    return parsed
