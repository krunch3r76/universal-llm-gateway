"""Hop-facing seam to the shared endpoint wake ledger (owner: agent_bus_store / 20995)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_seat.registry import normalize_bus_address


@dataclass(frozen=True)
class WakeClaimContext:
    """Dispatcher-held claim passed into the orion-chat-wake pipeline (no re-claim)."""

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
        return self.endpoint_url.split("?", 1)[0]


def wake_claim_from_options(opts: dict[str, Any] | None) -> WakeClaimContext | None:
    if not opts:
        return None
    wake_id = str(opts.get("wake_id") or "").strip()
    if not wake_id:
        return None
    thread_id = str(opts.get("thread") or "").strip()
    agent = str(opts.get("agent") or opts.get("wake_agent") or "orion").strip()
    endpoint = str(opts.get("conversation_url") or opts.get("endpoint_url") or "").strip()
    raw_turn_ids = opts.get("turn_ids") or opts.get("covered_turn_ids") or []
    if not raw_turn_ids and opts.get("turn_id") is not None:
        raw_turn_ids = [opts.get("turn_id")]
    turn_ids: list[int] = []
    for item in raw_turn_ids if isinstance(raw_turn_ids, list) else [raw_turn_ids]:
        try:
            turn_ids.append(int(item))
        except (TypeError, ValueError):
            continue
    turn_numbers: list[int] = []
    raw_turn_numbers = opts.get("turn_numbers") or opts.get("turns")
    if raw_turn_numbers is None and opts.get("turn") is not None:
        raw_turn_numbers = [opts.get("turn")]
    if isinstance(raw_turn_numbers, str) and "," in raw_turn_numbers:
        raw_turn_numbers = [p.strip() for p in raw_turn_numbers.split(",") if p.strip()]
    for item in raw_turn_numbers or []:
        try:
            turn_numbers.append(int(item))
        except (TypeError, ValueError):
            continue
    if not thread_id or not turn_ids or not endpoint:
        return None
    if not turn_numbers:
        turn_numbers = list(turn_ids)
    return WakeClaimContext(
        wake_id=wake_id,
        agent=agent,
        thread_id=thread_id,
        turn_ids=tuple(turn_ids),
        turn_numbers=tuple(turn_numbers),
        endpoint_url=endpoint.split("?", 1)[0],
        purpose=str(opts.get("purpose") or "pointer_wake"),
        binding_reference=(
            str(opts.get("binding_reference")).strip()
            if opts.get("binding_reference")
            else None
        ),
        attempt_id=str(opts.get("attempt_id")).strip()
        if opts.get("attempt_id")
        else None,
    )


def enrich_delivery_receipt(
    receipt: dict[str, Any], claim: WakeClaimContext
) -> dict[str, Any]:
    return {
        **receipt,
        "wake_id": claim.wake_id,
        "covered_turn_ids": list(claim.turn_ids),
        "turn_numbers": list(claim.turn_numbers),
        "agent": claim.normalized_agent(),
        "thread_id": claim.thread_id,
        "endpoint_url": claim.normalized_endpoint(),
        "purpose": claim.purpose,
        "binding_reference": claim.binding_reference,
        "attempt_id": claim.attempt_id,
    }


class OrionWakeDeliveryOwner(Protocol):
    """Shared ledger owner API (implemented in agent_bus_store when mounted)."""

    def validate_claim_for_delivery(self, claim: WakeClaimContext) -> dict[str, Any]:
        """Return ``{ok: True}`` when *claim* is an active dispatcher-held wake_id."""


@dataclass
class FailClosedWakeDeliveryOwner:
    """Default when the owner DB is not mounted in this process."""

    reason: str = "wake_owner_unconfigured"

    def validate_claim_for_delivery(self, claim: WakeClaimContext) -> dict[str, Any]:
        _ = claim
        return {"ok": False, "reason": self.reason}


@dataclass
class InMemoryWakeDeliveryOwner:
    """Test double: single active claim per wake_id; no re-claim in pipeline."""

    active_claims: dict[str, WakeClaimContext] = field(default_factory=dict)
    finished: set[str] = field(default_factory=set)

    def register_claim(self, claim: WakeClaimContext) -> None:
        self.active_claims[claim.wake_id] = claim

    def finish_claim(self, wake_id: str) -> None:
        self.finished.add(wake_id)
        self.active_claims.pop(wake_id, None)

    def validate_claim_for_delivery(self, claim: WakeClaimContext) -> dict[str, Any]:
        if claim.wake_id in self.finished:
            return {"ok": False, "reason": "wake_already_finished"}
        held = self.active_claims.get(claim.wake_id)
        if held is None:
            return {"ok": False, "reason": "wake_not_claimed"}
        if held.thread_id != claim.thread_id:
            return {"ok": False, "reason": "wake_claim_thread_mismatch"}
        if held.turn_ids != claim.turn_ids:
            return {"ok": False, "reason": "wake_claim_turn_mismatch"}
        held_endpoint = held.normalized_endpoint()
        if held_endpoint != claim.normalized_endpoint():
            return {"ok": False, "reason": "wake_claim_endpoint_mismatch"}
        return {"ok": True, "reason": "claim_valid"}


def load_shared_wake_delivery_owner() -> OrionWakeDeliveryOwner:
    """Resolve the owner adapter when agent_bus_store mounts the shared ledger."""
    try:
        from agent_bus_store.db import (
            orion_wake_owner_adapter,  # type: ignore[import-not-found]
        )
    except ImportError:
        return FailClosedWakeDeliveryOwner()
    factory = getattr(orion_wake_owner_adapter, "shared_wake_delivery_owner", None)
    if not callable(factory):
        return FailClosedWakeDeliveryOwner()
    owner = factory()
    if owner is None:
        return FailClosedWakeDeliveryOwner()
    return owner
