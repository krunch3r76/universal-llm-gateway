"""Bus subject for successful cursor-sdk closeout delivery."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_packet import infer_contract_from_text
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

_HANDOFF_TO_WIRE = {
    "pure-mechanical": "implement",
    "implement": "implement",
}


def _caller_explicitly_set_read_only(req: CursorDispatchRequest) -> bool:
    return "read_only" in req.model_fields_set


def _effective_read_only(req: CursorDispatchRequest, contract: str) -> bool:
    if _caller_explicitly_set_read_only(req):
        return req.read_only
    if contract == "implement":
        return False
    if contract == "consult":
        return True
    if contract == "light-bounded":
        return False
    return False


def resolve_closeout_wire_contract(
    req: CursorDispatchRequest, *, contract_fallback: str
) -> str:
    """Resolve wire contract token — not the cursor-sdk handoff class."""
    from_message = infer_contract_from_text(req.message or "")
    if from_message:
        return from_message
    handoff = (req.handoff_contract or "").strip().lower()
    mapped = _HANDOFF_TO_WIRE.get(handoff)
    if mapped:
        return mapped
    return (contract_fallback or handoff or "consult").strip().lower()


def build_sdk_closeout_subject(req: CursorDispatchRequest, *, contract: str) -> str:
    """Neutral CLOSEOUT label plus request-derived leg provenance only."""
    wire_contract = resolve_closeout_wire_contract(req, contract_fallback=contract)
    handoff = (req.handoff_contract or "").strip().lower()
    parts = [
        "cursor-sdk",
        "CLOSEOUT",
        req.dispatch_id,
        f"contract={wire_contract}",
    ]
    if handoff and handoff != wire_contract:
        parts.append(f"handoff={handoff}")
    if _effective_read_only(req, wire_contract):
        parts.append("read_only")
    if req.admitted_via:
        parts.append(f"admitted_via={req.admitted_via}")
    if req.caller_agent:
        parts.append(f"caller={req.caller_agent}")
    if req.nest_under:
        parts.append(f"nest={req.nest_under}")
    return " ".join(parts)


__all__ = ["build_sdk_closeout_subject", "resolve_closeout_wire_contract"]
