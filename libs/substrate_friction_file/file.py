"""POST cortex-api ``friction`` for substrate friction-file writes.

Callers are the MCP ``substrate_friction_file`` verb. The lib is the only
Cortex POST author so a later GIW adapter can import it without crossing
the mcp-server / git_integration_worker domain wall.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

_TIMEOUT = 30.0


def resolve_friction_note(*, note: str = "", claim: str = "") -> tuple[str | None, str | None]:
    """Resolve note/claim aliases; reject when both are non-empty and differ."""
    note_text = (note or "").strip()
    claim_text = (claim or "").strip()
    if note_text and claim_text and note_text != claim_text:
        return None, (
            "substrate_friction_file: supply note= or claim= (same slot), "
            "not both with different values"
        )
    resolved = note_text or claim_text
    return resolved or None, None


def file_friction(
    *,
    owner: str,
    note: str,
    category: str | None = None,
    suggestion: str | None = None,
    evidence_uris: list[str] | str | None = None,
    confidence: str | None = None,
    agent: str | None = None,
    seat: str = "cursor-sdk",
    via_adapter: bool = True,
    surface: str = "code",
) -> dict[str, Any]:
    """File *note* against *owner* via cortex-api ``/dispatch`` tool=friction.

    Does not mint missing owners — Cortex 404 passes through. Side effect is
    the HTTP POST only; no bus turn is enqueued.
    """
    arguments: dict[str, Any] = {
        "owner": owner.strip(),
        "note": note.strip(),
    }
    if category:
        arguments["category"] = category
    if suggestion:
        arguments["suggestion"] = suggestion
    if evidence_uris:
        arguments["evidence_uris"] = evidence_uris
    if confidence:
        arguments["confidence"] = confidence
    if agent:
        arguments["agent"] = agent
    body = {
        "tool": "friction",
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
