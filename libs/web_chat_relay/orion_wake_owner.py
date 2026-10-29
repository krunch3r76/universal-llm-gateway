"""Hop-facing seam to the shared endpoint wake ledger (owner: agent_bus_store / 20995)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

try:
    from agent_bus_store.db.orion_wake_owner_adapter import (
        LedgerWakeDeliveryOwner,
        WakeClaimContext,
        enrich_delivery_receipt,
        wake_claim_from_options,
    )
except ImportError:  # pragma: no cover - offline slices without agent_bus_store
    from dataclasses import dataclass as _dc

    @_dc(frozen=True)
    class WakeClaimContext:  # type: ignore[no-redef]
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
            from agent_seat.registry import normalize_bus_address

            return normalize_bus_address(self.agent)

        def normalized_endpoint(self) -> str:
            return str(self.endpoint_url or "").split("?", 1)[0].strip()

    def wake_claim_from_options(opts: dict[str, Any] | None) -> WakeClaimContext | None:
        return None

    def enrich_delivery_receipt(
        receipt: dict[str, Any], claim: WakeClaimContext
    ) -> dict[str, Any]:
        return {**receipt, "wake_id": claim.wake_id}

    LedgerWakeDeliveryOwner = Any  # type: ignore[misc, assignment]


class OrionWakeDeliveryOwner(Protocol):
    def validate_claim_for_delivery(self, claim: WakeClaimContext) -> dict[str, Any]: ...

    def prepare_paste_delivery(self, claim: WakeClaimContext) -> dict[str, Any]: ...

    def record_paste_receipt(
        self, claim: WakeClaimContext, receipt: dict[str, Any]
    ) -> None: ...


@dataclass
class FailClosedWakeDeliveryOwner:
    reason: str = "wake_owner_unconfigured"

    def validate_claim_for_delivery(self, claim: WakeClaimContext) -> dict[str, Any]:
        _ = claim
        return {"ok": False, "reason": self.reason}

    def prepare_paste_delivery(self, claim: WakeClaimContext) -> dict[str, Any]:
        _ = claim
        return {
            "ok": False,
            "reason": self.reason,
            "pasted": False,
            "delivered": False,
        }

    def record_paste_receipt(
        self, claim: WakeClaimContext, receipt: dict[str, Any]
    ) -> None:
        _ = claim, receipt


@dataclass
class InMemoryWakeDeliveryOwner:
    """Test double implementing prepare/record (no second persistence authority)."""

    active_claims: dict[str, WakeClaimContext] = field(default_factory=dict)
    finished: set[str] = field(default_factory=set)
    delivered: set[str] = field(default_factory=set)
    in_flight: set[str] = field(default_factory=set)
    paste_receipts: dict[str, dict[str, Any]] = field(default_factory=dict)

    def register_claim(self, claim: WakeClaimContext) -> None:
        self.active_claims[claim.wake_id] = claim

    def finish_claim(self, wake_id: str) -> None:
        self.finished.add(wake_id)
        self.active_claims.pop(wake_id, None)
        self.in_flight.discard(wake_id)

    def validate_claim_for_delivery(self, claim: WakeClaimContext) -> dict[str, Any]:
        if claim.wake_id in self.finished or claim.wake_id in self.delivered:
            return {"ok": False, "reason": "wake_already_finished"}
        held = self.active_claims.get(claim.wake_id)
        if held is None:
            return {"ok": False, "reason": "wake_not_claimed"}
        if held.thread_id != claim.thread_id:
            return {"ok": False, "reason": "wake_claim_thread_mismatch"}
        if held.turn_ids != claim.turn_ids:
            return {"ok": False, "reason": "wake_claim_turn_mismatch"}
        if held.normalized_endpoint() != claim.normalized_endpoint():
            return {"ok": False, "reason": "wake_claim_endpoint_mismatch"}
        return {"ok": True, "reason": "claim_valid"}

    def prepare_paste_delivery(self, claim: WakeClaimContext) -> dict[str, Any]:
        if claim.wake_id in self.delivered:
            first = self.paste_receipts.get(claim.wake_id, {})
            return {
                "ok": True,
                "reason": "duplicate_suppressed",
                "delivered": False,
                "pasted": False,
                "first_receipt": first,
            }
        if claim.wake_id in self.in_flight:
            return {
                "ok": False,
                "reason": "paste_in_flight",
                "pasted": False,
                "delivered": False,
            }
        gate = self.validate_claim_for_delivery(claim)
        if not gate.get("ok"):
            return {
                **gate,
                "ok": False,
                "pasted": False,
                "delivered": False,
            }
        self.in_flight.add(claim.wake_id)
        return {"ok": True, "claim": claim}

    def record_paste_receipt(
        self, claim: WakeClaimContext, receipt: dict[str, Any]
    ) -> None:
        self.in_flight.discard(claim.wake_id)
        if str(receipt.get("reason") or "") == "duplicate_suppressed":
            return
        self.paste_receipts[claim.wake_id] = dict(receipt)
        if receipt.get("delivered") is True:
            self.delivered.add(claim.wake_id)
            self.finish_claim(claim.wake_id)


def load_shared_wake_delivery_owner() -> OrionWakeDeliveryOwner:
    try:
        from agent_bus_store.db.orion_wake_owner_adapter import (
            shared_wake_delivery_owner,
        )
    except ImportError:
        return FailClosedWakeDeliveryOwner()
    return shared_wake_delivery_owner()
