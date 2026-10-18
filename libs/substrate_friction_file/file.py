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
from ulg_routing_headers.client import internal_dispatch_headers

_TIMEOUT = 30.0

# Omitted category used to 422 at cortex (`category` is required there) while
# the MCP harness advertised owner+note only (friction:39588 call 1).
_DEFAULT_FRICTION_CATEGORY = "tool_error"
_UNANCHORED_PROTOCOL_REASON = "filed via unanchored substrate_friction_file"


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
    actionable: bool | None = None,
    actionable_false_reason: str | None = None,
    seat: str = "cursor-sdk",
    caller: str = "substrate_friction_file",
    surface: str = "code",
) -> dict[str, Any]:
    """File *note* against *owner* via cortex-api ``/dispatch`` tool=friction.

    Omitted ``category`` becomes ``tool_error`` so a harness call that only
    has owner and note still reaches the assertion write. ``protocol`` with
    ``actionable`` unset is stored not-actionable: this verb has no charter
    or continuity anchor fields, and cortex rejects unanchored protocol when
    actionable is not false. Explicit ``actionable`` is forwarded unchanged.
    Does not mint missing owners — Cortex 404 passes through. A 200 body
    that carries ``error`` and no ``status_code`` is stamped 422 so callers
    do not log a validation rejection as status null. Side effect is the
    HTTP POST only; no bus turn is enqueued.
    """
    resolved_category = (category or "").strip() or _DEFAULT_FRICTION_CATEGORY
    if resolved_category == "protocol" and actionable is None:
        actionable = False
        if not (actionable_false_reason or "").strip():
            actionable_false_reason = _UNANCHORED_PROTOCOL_REASON
    arguments: dict[str, Any] = {
        "owner": owner.strip(),
        "note": note.strip(),
        "category": resolved_category,
    }
    if suggestion:
        arguments["suggestion"] = suggestion
    if evidence_uris:
        arguments["evidence_uris"] = evidence_uris
    if confidence:
        arguments["confidence"] = confidence
    if agent:
        arguments["agent"] = agent
    if actionable is not None:
        arguments["actionable"] = actionable
    reason = (actionable_false_reason or "").strip()
    if reason:
        arguments["actionable_false_reason"] = reason
    headers = internal_dispatch_headers(surface=surface, seat=seat, caller=caller)
    body = {
        "tool": "friction",
        "arguments": json.dumps(arguments),
        "surface": surface,
        "seat": seat,
    }
    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=_TIMEOUT) as client:
            response = client.post("/dispatch", json=body, headers=headers)
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
    if "error" in parsed and "status_code" not in parsed:
        parsed = dict(parsed)
        parsed["status_code"] = 422
    return parsed
