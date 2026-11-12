"""CDP reply mailbox resolution for agent_bus continuity hop commissions."""

from __future__ import annotations

from typing import Any


def resolve_hop_cdp_caller_agent(explicit_caller_agent: str | None) -> str | None:
    """Return deliverable ``caller_agent`` when explicitly supplied; never derive."""
    from systems.frontier_consult.cdp_admit_identity import is_valid_cdp_caller_agent

    cand = (explicit_caller_agent or "").strip()
    if not cand:
        return None
    if is_valid_cdp_caller_agent(cand):
        return cand
    return None


def hop_caller_agent_required_response() -> dict[str, Any]:
    """Refusal envelope before hop dispatches CDP generate without a wake mailbox."""
    return {
        "error": (
            "hop: caller_agent must name a deliverable bus mailbox "
            "for the CDP successor reply"
        ),
        "reason": "hop_caller_agent_required",
        "code": "HOP_CALLER_AGENT_REQUIRED",
        "fix_hint": (
            "pass caller_agent=<your bus mailbox>; the CDP reply and its "
            "wake are addressed to it"
        ),
    }
