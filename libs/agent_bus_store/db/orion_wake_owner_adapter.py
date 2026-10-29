"""In-process owner API for Orion / shared endpoint wake claims (20995 ledger)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_seat.registry import normalize_bus_address

from .connection import connect
from .endpoint_wake_ledger import resolve_wake_delivery_binding

_ORION_WAKE_HOUSE = "21207"


@dataclass(frozen=True)
class WakeClaimContext:
    """Dispatcher-held claim passed into orion-chat-wake (no re-claim in pipeline)."""

    wake_id: str
    agent: str
    thread_id: str
    turn_ids: tuple[int, ...]
    turn_numbers: tuple[int, ...]
    endpoint_url: str
    purpose: str = "pointer_wake"
    binding_reference: str | None = None
    attempt_id: str | None = None

    def normalized_agent(self) -> str:
        return normalize_bus_address(self.agent)

    def normalized_endpoint(self) -> str:
        return str(self.endpoint_url or "").split("?", 1)[0].strip()


def _endpoint_from_actuation(actuation: dict[str, Any]) -> str:
    atype = str(actuation.get("type") or "")
    if atype == "orion_chat":
        return str(actuation.get("conversation_url") or "").split("?", 1)[0].strip()
    if atype == "cursor_glass":
        return str(actuation.get("agent_id") or "").strip()
    return ""


class LedgerWakeDeliveryOwner:
    """Validate pipeline delivery against claimed rows in endpoint_wake_ledger."""

    def validate_claim_for_delivery(self, claim: WakeClaimContext) -> dict[str, Any]:
        if not str(claim.wake_id or "").strip():
            return {"ok": False, "reason": "wake_id_required"}
        if not claim.turn_ids:
            return {"ok": False, "reason": "turn_ids_required"}
        if not str(claim.endpoint_url or "").strip():
            return {"ok": False, "reason": "endpoint_required"}
        if not str(claim.thread_id or "").strip():
            return {"ok": False, "reason": "thread_id_required"}

        want_agent = claim.normalized_agent()
        want_endpoint = claim.normalized_endpoint()
        want_turns = tuple(sorted(int(t) for t in claim.turn_ids))

        with connect() as conn:
            rows = conn.execute(
                "SELECT agent, thread_id, turn_id, state FROM endpoint_wake_ledger "
                "WHERE wake_id=?",
                (claim.wake_id,),
            ).fetchall()

        claimed = [dict(r) for r in rows if str(r["state"]) == "claimed"]
        if not claimed:
            return {"ok": False, "reason": "wake_not_claimed"}

        ledger_turns = tuple(sorted(int(r["turn_id"]) for r in claimed))
        if ledger_turns != want_turns:
            return {"ok": False, "reason": "wake_claim_turn_mismatch"}

        ledger_thread = str(claimed[0]["thread_id"])
        if ledger_thread != str(claim.thread_id):
            return {"ok": False, "reason": "wake_claim_thread_mismatch"}

        for row in claimed:
            if normalize_bus_address(str(row["agent"])) != want_agent:
                return {"ok": False, "reason": "wake_claim_agent_mismatch"}

        binding = resolve_wake_delivery_binding(house_id=claim.thread_id)
        if str(binding.get("state") or "") != "bound":
            return {"ok": False, "reason": "delivery_unbound"}

        actuation = dict((binding.get("current") or {}).get("actuation") or {})
        bound_endpoint = _endpoint_from_actuation(actuation)
        if claim.thread_id == _ORION_WAKE_HOUSE:
            if bound_endpoint and want_endpoint != bound_endpoint:
                return {"ok": False, "reason": "wake_claim_endpoint_mismatch"}
        elif bound_endpoint and want_endpoint and want_endpoint != bound_endpoint:
            return {"ok": False, "reason": "wake_claim_endpoint_mismatch"}

        return {"ok": True, "reason": "claim_valid"}


def shared_wake_delivery_owner() -> LedgerWakeDeliveryOwner:
    """Factory for agent_bus_store-mounted processes (Orion hop seam)."""
    return LedgerWakeDeliveryOwner()


__all__ = [
    "LedgerWakeDeliveryOwner",
    "WakeClaimContext",
    "shared_wake_delivery_owner",
]
