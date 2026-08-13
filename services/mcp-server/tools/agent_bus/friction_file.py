"""agent_bus ``substrate_friction_file`` — cortex friction request-surface verb.

Life and code seats call this instead of dumping friction as bus prose.
The module validates owner+note, then delegates the Cortex POST to
``substrate_friction_file.file_friction`` so mcp-server never authors the
HTTP body itself.
"""

from __future__ import annotations

from typing import Any

from mcp_events import record
from substrate_friction_file import file_friction, resolve_friction_note


def _reject(reason: str, *, message: str) -> dict[str, Any]:
    record("mcp.agentbus.friction_file.rejected", reason=reason)
    return {"error": message, "reason": reason, "status_code": 422}


def _friction_file_dispatch(
    *,
    owner: str = "",
    service: str = "",
    note: str = "",
    claim: str = "",
    category: str | None = None,
    suggestion: str | None = None,
    evidence_uris: list[str] | str | None = None,
    confidence: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Validate + dispatch ``agent_bus.substrate_friction_file``.

    Requires ``owner`` (or ``service`` alias) and ``note`` (or ``claim``
    alias); POSTs cortex ``friction`` via the shared
    ``substrate_friction_file`` lib. Does not mint owners on 404 or enqueue
    bus turns. Hop/request envelope fields are rejected by the dispatch
    unknown-argument gate (not in this signature).
    """
    resolved_owner = (owner or service or "").strip()
    resolved_note, alias_err = resolve_friction_note(note=note, claim=claim)
    if alias_err:
        return _reject("friction_file_note_claim_conflict", message=alias_err)
    if not resolved_owner:
        return _reject(
            "friction_file_owner_required",
            message="substrate_friction_file: owner is required (service= alias accepted)",
        )
    if not resolved_note:
        return _reject(
            "friction_file_note_required",
            message="substrate_friction_file: note is required (claim= alias accepted)",
        )

    result = file_friction(
        owner=resolved_owner,
        note=resolved_note,  # claim= alias already resolved above
        category=category,
        suggestion=suggestion,
        evidence_uris=evidence_uris,
        confidence=confidence,
        agent=agent,
        seat="mcp",
        via_adapter=True,
        surface="code",
    )
    if "error" in result:
        record(
            "mcp.agentbus.friction_file.failed",
            owner=resolved_owner,
            status_code=result.get("status_code"),
        )
        return result

    item = result.get("item") if isinstance(result.get("item"), dict) else {}
    assertion_id = item.get("id") or result.get("assertion_id")
    stamped = dict(result)
    stamped["owner"] = resolved_owner
    if assertion_id is not None:
        stamped["assertion_id"] = assertion_id
    record(
        "mcp.agentbus.friction_file.posted",
        owner=resolved_owner,
        assertion_id=assertion_id,
    )
    return stamped
