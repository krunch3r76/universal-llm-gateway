"""CDP reply mailbox resolution for agent_bus continuity hop commissions."""

from __future__ import annotations

from typing import Any


def resolve_hop_cdp_caller_agent(explicit_caller_agent: str | None) -> str | None:
    """Return deliverable ``caller_agent`` when explicitly supplied; never derive."""
    from cdp_caller_identity import is_valid_cdp_caller_agent

    cand = (explicit_caller_agent or "").strip()
    if not cand:
        return None
    if is_valid_cdp_caller_agent(cand):
        return cand
    return None


def hop_invalid_caller_agent_response(caller_agent: str) -> dict[str, Any]:
    """Refusal when caller_agent is present but not a deliverable mailbox."""
    return {
        "error": (
            f"hop: caller_agent {caller_agent!r} is not a deliverable bus mailbox"
        ),
        "reason": "hop_caller_agent_invalid",
        "code": "HOP_CALLER_AGENT_INVALID",
        "fix_hint": (
            "pass caller_agent=<your bus mailbox>; accepted form is a non-empty "
            "deliverable seat address (e.g. cursor-sdk-lane, cursor-brg-…)"
        ),
    }


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


def resolve_hop_work_key(
    thread_id: str,
    explicit_work_key: str | None,
) -> str | None:
    """Hop admit work_key: explicit argument, else latest hop_precedence row for lane."""
    key = (explicit_work_key or "").strip()
    if key:
        return key
    try:
        from agent_bus_store.db.connection import connect
        from agent_bus_store.db.hop_precedence import get_hop_precedence

        with connect() as conn:
            rows = get_hop_precedence(conn, thread_id=thread_id)
        if rows:
            row_key = (rows[-1].get("work_key") or "").strip()
            if row_key:
                return row_key
    except Exception:  # noqa: BLE001 — omit when store unavailable
        return None
    return None
