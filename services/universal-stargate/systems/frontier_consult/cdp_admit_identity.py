"""CDP generate admit identity checks (a:43747 D4/D6)."""

from __future__ import annotations

from typing import Any

from bus_watch.endpoint_wake_dispatcher import (
    _GATE_WAKE_REMAP_ADDRESSES,
    _ROUTINE_DISPATCH_FROM,
)
from claude_bundles.cdp_model_endpoint import CDP_REPLY_FROM
from claude_bundles.sealed_cdp_prefix import REVIEW_CLASS_CONTRACTS
from work_key_grammar import is_valid_work_key_scheme

from .admission import FrontierEndpointError

_WORK_KEY_UTF8_MAX = 256

# Non-mailbox targets: wake remap heads (endpoint_wake_dispatcher.py), routine
# dispatch from addresses (same module), and CDP self-reply alias (CDP_REPLY_FROM).
_PSEUDO_CALLER_ADDRESSES = frozenset(
    addr.casefold()
    for addr in (
        *_GATE_WAKE_REMAP_ADDRESSES,
        *_ROUTINE_DISPATCH_FROM,
        CDP_REPLY_FROM,
        "unspecified",
        "cursor",
        "all",
    )
)


def normalize_cdp_caller_agent(raw: object) -> str:
    """Return stripped caller_agent string (may be empty)."""
    return str(raw or "").strip()


def is_valid_cdp_caller_agent(raw: object) -> bool:
    """True when ``raw`` is a non-empty bus mailbox, not a pseudo address."""
    val = normalize_cdp_caller_agent(raw)
    if not val:
        return False
    return val.casefold() not in _PSEUDO_CALLER_ADDRESSES


def resolve_cdp_reply_to_agent(caller_agent: str | None) -> str | None:
    """Normalize ``caller_agent`` for on-behalf delivery; None when unusable."""
    if not is_valid_cdp_caller_agent(caller_agent):
        return None
    return normalize_cdp_caller_agent(caller_agent)


def validate_work_key_if_supplied(body: Any, *, request_id: str) -> str | None:
    """Fail closed on malformed optional ``work_key`` before spawn."""
    raw = getattr(body, "work_key", None)
    if raw is None:
        return None
    key = str(raw).strip()
    if not key:
        return None
    if len(key.encode("utf-8")) > _WORK_KEY_UTF8_MAX:
        raise FrontierEndpointError(
            request_id=request_id,
            field="work_key",
            reason="work_key exceeds 256 UTF-8 bytes",
            status_code=422,
            code="work_key_unparseable",
        )
    if not is_valid_work_key_scheme(key):
        raise FrontierEndpointError(
            request_id=request_id,
            field="work_key",
            reason=(
                "work_key must use a scheme prefix "
                "(todo:, plan:, plan_phase:, packet:, agent-bus:, friction:, decision:)"
            ),
            status_code=422,
            code="work_key_unparseable",
        )
    return key


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
        validate_work_key_if_supplied(body, request_id=request_id)
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
