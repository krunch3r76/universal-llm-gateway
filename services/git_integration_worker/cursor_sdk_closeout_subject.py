"""Bus subject for successful cursor-sdk closeout delivery."""

from __future__ import annotations

from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


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


def build_sdk_closeout_subject(req: CursorDispatchRequest, *, contract: str) -> str:
    """Neutral CLOSEOUT label plus request-derived leg provenance only."""
    contract_norm = (contract or "consult").strip().lower()
    parts = ["cursor-sdk", "CLOSEOUT", req.dispatch_id, f"contract={contract_norm}"]
    if _effective_read_only(req, contract_norm):
        parts.append("read_only")
    if req.admitted_via:
        parts.append(f"admitted_via={req.admitted_via}")
    if req.caller_agent:
        parts.append(f"caller={req.caller_agent}")
    if req.nest_under:
        parts.append(f"nest={req.nest_under}")
    return " ".join(parts)


__all__ = ["build_sdk_closeout_subject"]
