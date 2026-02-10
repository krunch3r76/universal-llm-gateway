"""
Concurrency capacity ledger — authoritative admission state.

Single-writer: all mutations on one async event loop (no locks needed).

INVARIANT: ∀ slot: in_flight[slot] ≥ 0
INVARIANT: ∀ request_id: reserve() called exactly once ⟹ release() called exactly once
"""

from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CapacitySlot:
    """Unique identifier for a (gateway, model) capacity slot."""

    gateway_id: str
    model_id: str


class CapacityLedger:
    """
    Tracks per-(gateway, model) concurrency capacity and in-flight requests.

    Single-writer design: all methods are synchronous (no await) and must
    be called from the same event loop thread. No locks needed.
    """

    def __init__(self) -> None:
        self._capacity: dict[CapacitySlot, int] = {}
        self._in_flight: dict[CapacitySlot, int] = {}
        self._reservations: dict[str, CapacitySlot] = {}

    def set_capacity(self, gateway_id: str, model_id: str, max_concurrent: int) -> None:
        """Set/update capacity from telemetry. Idempotent."""
        if max_concurrent < 0:
            logger.error(
                f"Invalid capacity {max_concurrent} for {gateway_id}/{model_id}"
            )
            max_concurrent = 0
        slot = CapacitySlot(gateway_id=gateway_id, model_id=model_id)
        old_capacity = self._capacity.get(slot, 0)
        self._capacity[slot] = max_concurrent
        self._in_flight.setdefault(slot, 0)
        if old_capacity != max_concurrent:
            logger.info(
                f"Capacity: {gateway_id}/{model_id}: {old_capacity} → {max_concurrent}"
            )

    def remove_gateway(self, gateway_id: str) -> None:
        """Remove all capacity for a disconnected gateway."""
        slots_to_remove = [
            slot for slot in self._capacity if slot.gateway_id == gateway_id
        ]
        for slot in slots_to_remove:
            in_flight = self._in_flight.get(slot, 0)
            if in_flight > 0:
                logger.warning(
                    f"Gateway {gateway_id} removed with {in_flight} in-flight "
                    f"on {slot.model_id}"
                )
            del self._capacity[slot]
            del self._in_flight[slot]
        for req_id, slot in list(self._reservations.items()):
            if slot.gateway_id == gateway_id:
                del self._reservations[req_id]
        if slots_to_remove:
            logger.info(f"Removed {len(slots_to_remove)} slots for {gateway_id}")

    def remove_model(self, gateway_id: str, model_id: str) -> None:
        """Remove capacity for an unloaded model on a gateway."""
        slot = CapacitySlot(gateway_id=gateway_id, model_id=model_id)
        if slot not in self._capacity:
            return
        in_flight = self._in_flight.get(slot, 0)
        if in_flight > 0:
            logger.warning(
                f"Model {model_id} on {gateway_id} removed with {in_flight} in-flight"
            )
        del self._capacity[slot]
        del self._in_flight[slot]
        for req_id, s in list(self._reservations.items()):
            if s == slot:
                del self._reservations[req_id]
        logger.info(f"Removed capacity: {gateway_id}/{model_id}")

    def try_reserve(self, request_id: str, gateway_id: str, model_id: str) -> bool:
        """Reserve one slot. Returns True if available. Idempotent."""
        slot = CapacitySlot(gateway_id=gateway_id, model_id=model_id)
        if request_id in self._reservations:
            if self._reservations[request_id] == slot:
                return True
            logger.error(f"Request {request_id} already on different slot")
            return False
        capacity = self._capacity.get(slot, 0)
        in_flight = self._in_flight.get(slot, 0)
        if in_flight >= capacity:
            return False
        self._in_flight[slot] = in_flight + 1
        self._reservations[request_id] = slot
        if self._in_flight[slot] > capacity:
            logger.error(
                f"Invariant: in_flight {self._in_flight[slot]} > capacity {capacity}"
            )
        return True

    def available(self, gateway_id: str, model_id: str) -> int:
        """Available slots = capacity - in_flight. Returns 0 if unknown."""
        slot = CapacitySlot(gateway_id=gateway_id, model_id=model_id)
        capacity = self._capacity.get(slot, 0)
        in_flight = self._in_flight.get(slot, 0)
        return max(0, capacity - in_flight)

    def get_available_gateways(self, model_id: str) -> list[tuple[str, int]]:
        """Return [(gateway_id, available)] for available > 0, sorted desc."""
        results: list[tuple[str, int]] = []
        for slot in self._capacity:
            if slot.model_id == model_id:
                avail = self.available(slot.gateway_id, slot.model_id)
                if avail > 0:
                    results.append((slot.gateway_id, avail))
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def release(self, request_id: str) -> bool:
        """Release slot. Returns True if found. Idempotent."""
        slot = self._reservations.pop(request_id, None)
        if slot is None:
            return False
        in_flight = self._in_flight.get(slot, 0)
        if in_flight <= 0:
            logger.error(f"Invariant: releasing {request_id} but in_flight=0")
            return True
        self._in_flight[slot] = in_flight - 1
        return True

    def get_snapshot(self) -> dict[str, Any]:
        """Return diagnostic snapshot of all capacity state."""
        return {
            "capacity": {
                f"{s.gateway_id}/{s.model_id}": c for s, c in self._capacity.items()
            },
            "in_flight": {
                f"{s.gateway_id}/{s.model_id}": c for s, c in self._in_flight.items()
            },
            "reservations": {
                r: f"{s.gateway_id}/{s.model_id}" for r, s in self._reservations.items()
            },
            "total_capacity": sum(self._capacity.values()),
            "total_in_flight": sum(self._in_flight.values()),
            "total_reservations": len(self._reservations),
        }
