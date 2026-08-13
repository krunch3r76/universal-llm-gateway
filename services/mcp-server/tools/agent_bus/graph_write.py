"""agent_bus ``substrate_graph_write`` — cortex assert request-surface verb."""

from __future__ import annotations

from typing import Any

from mcp_events import record
from substrate_graph_write import write_claim


def _reject(reason: str, *, message: str) -> dict[str, Any]:
    record("mcp.agentbus.graph_write.rejected", reason=reason)
    return {"error": message, "reason": reason, "status_code": 422}


def _graph_write_dispatch(
    *,
    entity_id: str = "",
    claim: str = "",
    confidence: str | None = None,
    derivation_type: str | None = None,
    evidence: str | None = None,
    evidence_uris: list[str] | str | None = None,
) -> dict[str, Any]:
    """Validate + dispatch ``agent_bus.substrate_graph_write``.

    Requires ``entity_id`` and ``claim``; POSTs cortex ``assert`` via the
    shared ``substrate_graph_write`` lib. Does not mint entities on 404 or
    enqueue bus turns. Hop/request envelope fields are rejected by the dispatch
    unknown-argument gate (not in this signature).
    """
    resolved_entity = (entity_id or "").strip()
    resolved_claim = (claim or "").strip()
    if not resolved_entity:
        return _reject(
            "graph_write_entity_required",
            message="substrate_graph_write: entity_id is required",
        )
    if not resolved_claim:
        return _reject(
            "graph_write_claim_required",
            message="substrate_graph_write: claim is required",
        )

    result = write_claim(
        entity_id=resolved_entity,
        claim=resolved_claim,
        confidence=confidence or "confirmed",
        derivation_type=derivation_type or "direct_observation",
        evidence=evidence,
        evidence_uris=evidence_uris,
        seat="mcp",
        via_adapter=True,
        surface="code",
    )
    if "error" in result:
        record(
            "mcp.agentbus.graph_write.failed",
            entity_id=resolved_entity,
            status_code=result.get("status_code"),
        )
        return result

    item = result.get("item") if isinstance(result.get("item"), dict) else {}
    assertion_id = item.get("id") or result.get("assertion_id")
    stamped = dict(result)
    stamped["entity_id"] = resolved_entity
    if assertion_id is not None:
        stamped["assertion_id"] = assertion_id
    record(
        "mcp.agentbus.graph_write.posted",
        entity_id=resolved_entity,
        assertion_id=assertion_id,
    )
    return stamped
