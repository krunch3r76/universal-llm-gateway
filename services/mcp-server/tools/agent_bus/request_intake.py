"""Observed intake steps for ``agent_bus.request`` — contract + request_id.

Wraps the pure vocabulary in :mod:`contract_vocab` with the observation events
the dispatch layer owes: a rejection counter for unknown contracts and a
per-caller deprecation counter that makes remaining ``consult`` authors
enumerable (Fable §6 Phase 1) instead of guessed.

Also owns the ``request_id`` idempotency key (Fable §5): every request carries
one — minted here when the caller omits it — so enqueue and closeout can be
correlated, and a caller replaying its own key is refused rather than silently
dispatched twice. The replay window is a bounded in-process ring
(:data:`_REGISTRY_MAX`), not a durable store: it catches the retry-loop case the
primitive exists for and cannot claim protection beyond this process.

Sender additive discipline: new intake/enqueue wire fields must ship with defaults
so older GIW receivers tolerate deploy-order skew (harvest-restart-propagation I3).
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from mcp_events import record

from .contract_vocab import ContractIntake, normalize_wire_contract

_REGISTRY_MAX = 4096
_REQUEST_ID_REGISTRY: OrderedDict[str, dict[str, Any]] = OrderedDict()


@dataclass(frozen=True, slots=True)
class RequestIdIntake:
    """One resolved ``request_id``: the key to echo, or a duplicate refusal."""

    request_id: str
    caller_supplied: bool
    error: dict[str, Any] | None = None


def _remember(request_id: str, row: dict[str, Any]) -> None:
    """Record *request_id* in the bounded replay window, evicting the oldest."""
    _REQUEST_ID_REGISTRY[request_id] = row
    while len(_REQUEST_ID_REGISTRY) > _REGISTRY_MAX:
        _REQUEST_ID_REGISTRY.popitem(last=False)


def resolve_request_id_intake(
    request_id: str | None,
    *,
    thread_id: str | None,
    contract: str,
    from_agent: str,
) -> RequestIdIntake:
    """Mint or validate a caller ``request_id`` before the turn is written.

    A minted key is unique by construction, so only caller-supplied keys enter
    the replay window — minting never consumes dedupe capacity.
    """
    raw = (request_id or "").strip()
    if not raw:
        return RequestIdIntake(request_id=str(uuid.uuid4()), caller_supplied=False)
    if raw in _REQUEST_ID_REGISTRY:
        record(
            "mcp.agentbus.request.rejected",
            reason="duplicate_request_id",
            request_id=raw,
            caller=from_agent,
        )
        return RequestIdIntake(
            request_id=raw,
            caller_supplied=True,
            error={
                "error": f"request: duplicate request_id={raw!r}",
                "reason": "duplicate_request_id",
                "status_code": 422,
                "request_id": raw,
            },
        )
    _remember(
        raw,
        {"thread_id": thread_id, "contract": contract, "from_agent": from_agent},
    )
    return RequestIdIntake(request_id=raw, caller_supplied=True)


def resolve_contract_intake(
    contract: str | None,
    *,
    from_agent: str,
) -> ContractIntake:
    """Normalize the wire contract and emit the matching observation event."""
    intake = normalize_wire_contract(contract)
    if intake.error is not None:
        record(
            "mcp.agentbus.request.rejected",
            reason="contract_unknown",
            contract=intake.requested,
            caller=from_agent,
        )
    elif intake.deprecated:
        record(
            "mcp.agentbus.request.contract_deprecated",
            contract=intake.requested,
            replacement=intake.contract,
            caller=from_agent,
        )
    return intake


_CHECKOUT_LANES = frozenset({"A", "B"})


def resolve_checkout_lane(
    lane: str | None,
    *,
    from_agent: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """Validate optional GIW checkout-isolation ``lane`` (``A``|``B``).

    Distinct from ``lane_role`` (bus-thread parentage) and from tag
    ``lane:cursor-auto``. Empty/None means omit — ``select_lane`` defaults
    stay unchanged. ``B`` requires a materialized worktree at GIW admit
    (else ``CURSOR_LANE_B_WORKTREE_MISSING``). Invalid values reject before
    the turn is written.
    """
    raw = (lane or "").strip()
    if not raw:
        return None, None
    normalized = raw.upper()
    if normalized not in _CHECKOUT_LANES:
        record(
            "mcp.agentbus.request.rejected",
            reason="request_lane_invalid",
            lane=raw,
            caller=from_agent,
        )
        return None, {
            "error": (
                "request: lane must be 'A' or 'B' (GIW checkout isolation); "
                f"got {raw!r}. Omit for default select_lane. Distinct from "
                "lane_role (bus-thread parentage)."
            ),
            "reason": "request_lane_invalid",
            "status_code": 422,
            "field": "lane",
            "provided": raw,
        }
    return normalized, None


def stamp_contract_deprecation(
    result: dict[str, Any],
    intake: ContractIntake,
) -> dict[str, Any]:
    """Add the deprecation note to a successful response, in place."""
    if intake.deprecated and "error" not in result:
        result["notes"] = intake.deprecation_note
        result["_deprecated"] = {
            "param": "contract",
            "value": intake.requested,
            "replacement": intake.contract,
        }
    return result


def reset_request_id_registry_for_tests() -> None:
    """Clear the replay window so suites do not leak keys into each other."""
    _REQUEST_ID_REGISTRY.clear()


__all__ = [
    "RequestIdIntake",
    "resolve_checkout_lane",
    "resolve_contract_intake",
    "resolve_request_id_intake",
    "reset_request_id_registry_for_tests",
    "stamp_contract_deprecation",
]
