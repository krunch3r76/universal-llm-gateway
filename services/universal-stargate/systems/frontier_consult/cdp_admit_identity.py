"""CDP generate admit identity checks (a:43747 D4/D6)."""

from __future__ import annotations

from typing import Any

from claude_bundles.sealed_cdp_prefix import REVIEW_CLASS_CONTRACTS

from .admission import FrontierEndpointError

_NON_ADDRESS_CALLERS = frozenset({"dispatch", "unspecified"})


def normalize_cdp_caller_agent(raw: object) -> str:
    """Return stripped caller_agent string (may be empty)."""
    return str(raw or "").strip()


def is_valid_cdp_caller_agent(raw: object) -> bool:
    """True when ``raw`` is a non-empty bus mailbox, not a pseudo address."""
    val = normalize_cdp_caller_agent(raw)
    return bool(val) and val not in _NON_ADDRESS_CALLERS


def resolve_cdp_reply_to_agent(caller_agent: str | None) -> str | None:
    """Normalize ``caller_agent`` for on-behalf delivery; None when unusable."""
    if not is_valid_cdp_caller_agent(caller_agent):
        return None
    return normalize_cdp_caller_agent(caller_agent)


def require_cdp_caller_agent(body: Any, *, request_id: str) -> str:
    """Fail closed before spawn when ``caller_agent`` is missing or pseudo."""
    val = normalize_cdp_caller_agent(getattr(body, "caller_agent", None))
    if not is_valid_cdp_caller_agent(val):
        raise FrontierEndpointError(
            request_id=request_id,
            field="caller_agent",
            reason=(
                "CDP generate requires caller_agent=<your bus mailbox>; "
                "the reply and its wake are addressed to it"
            ),
            status_code=422,
            code="cdp_caller_agent_required",
            details={
                "fix_hint": (
                    "pass caller_agent=<your bus mailbox>; the reply and its "
                    "wake are addressed to it"
                ),
            },
        )
    return val


def require_cdp_review_work_key(
    body: Any, contract: str | None, *, request_id: str
) -> None:
    """Review-class CDP generate must carry work_key for ledger bind."""
    contract_key = (contract or "").strip().lower()
    if contract_key not in REVIEW_CLASS_CONTRACTS:
        return
    work_key = str(getattr(body, "work_key", None) or "").strip()
    if work_key:
        return
    thread = str(getattr(body, "dispatch_thread_id", "") or "").strip()
    fix = f"agent-bus:{thread}" if thread else "agent-bus:<thread>"
    raise FrontierEndpointError(
        request_id=request_id,
        field="work_key",
        reason="review-class CDP generate requires work_key for ledger attribution",
        status_code=422,
        code="cdp_review_work_key_required",
        details={"fix_hint": fix},
    )
