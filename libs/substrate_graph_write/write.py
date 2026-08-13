"""POST cortex-api ``assert`` for substrate graph writes."""

from __future__ import annotations

import json
from typing import Any

import httpx
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

_DEFAULT_EVIDENCE = "substrate_graph_write"
_DEFAULT_CONFIDENCE = "confirmed"
_DEFAULT_DERIVATION = "direct_observation"
_TIMEOUT = 30.0


def write_claim(
    *,
    entity_id: str,
    claim: str,
    confidence: str = _DEFAULT_CONFIDENCE,
    derivation_type: str = _DEFAULT_DERIVATION,
    evidence: str | None = None,
    evidence_uris: list[str] | str | None = None,
    seat: str = "cursor-sdk",
    via_adapter: bool = True,
    surface: str = "code",
) -> dict[str, Any]:
    """Assert *claim* onto *entity_id* via cortex-api ``/dispatch`` tool=assert."""
    arguments: dict[str, Any] = {
        "entity_id": entity_id.strip(),
        "claim": claim.strip(),
        "confidence": confidence,
        "derivation_type": derivation_type,
        "evidence": (evidence or "").strip() or _DEFAULT_EVIDENCE,
    }
    if evidence_uris:
        arguments["evidence_uris"] = evidence_uris
    body = {
        "tool": "assert",
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
