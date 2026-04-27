"""Mixin: capacity bookkeeping — set/remove slots, reconcile, query availability."""

from __future__ import annotations

import asyncio

from universal_logging import get_logger

from ._types import _Slot

logger = get_logger(__name__)


class _BookkeepingMixin:
    async def reconcile_gateway_state(
        self,
        gateway_id: str,
        idle_model_ids: set[str],
    ) -> None:
        """Reset stale in_flight counts for models confirmed idle by telemetry.

        Called by the gateway manager on GATEWAY_SNAPSHOT when the snapshot
        reports a model as loaded-but-not-busy.  If the capacity pool shows
        in_flight > 0 for such a model it means capacity tokens were leaked
        (e.g., capacity token not released on CancelledError / client disconnect).

        After resetting, dispatch is called for each affected model so queued
        waiters can immediately claim the recovered slots.

        INVARIANT: ∀ (gateway, model) ∈ idle_model_ids ∧ in_flight > 0:
            the excess in_flight counts are stale — no live request holds them.
        Safety: GATEWAY_SNAPSHOT arrives every ~120 s.  Any request admitted in
        the last 120 s would either have completed (token released) or still be
        active (gateway would report it in busy_models, excluded from this set).
        """
        recovered_models: list[str] = []
        for model_id in idle_model_ids:
            slot = _Slot(gateway_id=gateway_id, model_id=model_id)
            in_flight = self._in_flight.get(slot, 0)  # type: ignore[attr-defined]
            if in_flight > 0:
                self._in_flight[slot] = 0  # type: ignore[attr-defined]
                recovered_models.append(model_id)
                logger.warning(
                    "Recovered %d leaked slot(s) for %s/%s "
                    "(gateway reports idle, pool had in_flight=%d)",
                    in_flight,
                    gateway_id,
                    model_id,
                    in_flight,
                )
        if recovered_models:
            total = len(recovered_models)
            logger.warning(
                "Capacity reconcile: gateway=%s recovered leaked slots for %d model(s)",
                gateway_id,
                total,
            )
            for model_id in recovered_models:
                await self._dispatch(model_id)  # type: ignore[attr-defined]

    def set_capacity(
        self,
        gateway_id: str,
        model_id: str,
        max_concurrent: int,
    ) -> None:
        """Set or update max concurrent capacity for a slot.

        Called by the gateway manager when telemetry reports a capacity
        change for a (gateway, model) pair.
        Idempotent — re-setting the same value is a no-op (no log emitted).
        Negative values are clamped to 0 with an ERROR log.
        """
        if max_concurrent < 0:
            logger.error(
                f"Invalid capacity {max_concurrent} for {gateway_id}/{model_id}"
            )
            max_concurrent = 0
        slot = _Slot(gateway_id=gateway_id, model_id=model_id)
        old = self._capacity.get(slot, 0)  # type: ignore[attr-defined]
        self._capacity[slot] = max_concurrent  # type: ignore[attr-defined]
        self._in_flight.setdefault(slot, 0)  # type: ignore[attr-defined]
        if old != max_concurrent:
            logger.info(f"Capacity: {gateway_id}/{model_id}: {old} → {max_concurrent}")

        if max_concurrent > old and model_id in self._queues:  # type: ignore[attr-defined]
            asyncio.create_task(
                self._dispatch(model_id),  # type: ignore[attr-defined]
                name=f"capacity-set-dispatch-{gateway_id}-{model_id}",
            )

    def remove_gateway(self, gateway_id: str) -> None:
        """Remove all capacity slots for a disconnected gateway.

        Physical deletion: _capacity and _in_flight entries for the gateway.
        Deferred: slots with in_flight > 0 are zeroed in _capacity; _release
        removes them when in_flight drains to 0.
        """
        slots = [s for s in self._capacity if s.gateway_id == gateway_id]  # type: ignore[attr-defined]
        removed = 0
        deferred = 0
        for slot in slots:
            in_flight = self._in_flight.get(slot, 0)  # type: ignore[attr-defined]
            if in_flight > 0:
                self._capacity[slot] = 0  # type: ignore[attr-defined]
                deferred += 1
                logger.warning(
                    "Gateway %s: slot %s zeroed with %d in-flight (deferred removal)",
                    gateway_id,
                    slot.model_id,
                    in_flight,
                )
            else:
                del self._capacity[slot]  # type: ignore[attr-defined]
                self._in_flight.pop(slot, None)  # type: ignore[attr-defined]
                removed += 1
        if slots:
            logger.info(
                "Gateway %s: %d slot(s) removed, %d deferred",
                gateway_id,
                removed,
                deferred,
            )

    def remove_model(self, gateway_id: str, model_id: str) -> None:
        """Mark a model's capacity as zero after telemetry reports unload.

        Physical deletion: _capacity and _in_flight for this (gateway_id, model_id).
        Deferred: if in_flight > 0, slot is zeroed in _capacity; _release
        removes it when in_flight drains to 0.
        """
        slot = _Slot(gateway_id=gateway_id, model_id=model_id)
        if slot not in self._capacity:  # type: ignore[attr-defined]
            return
        in_flight = self._in_flight.get(slot, 0)  # type: ignore[attr-defined]
        if in_flight > 0:
            self._capacity[slot] = 0  # type: ignore[attr-defined]
            logger.warning(
                "Model %s on %s: capacity zeroed with %d in-flight "
                "(deferred removal until drained)",
                model_id,
                gateway_id,
                in_flight,
            )
        else:
            del self._capacity[slot]  # type: ignore[attr-defined]
            self._in_flight.pop(slot, None)  # type: ignore[attr-defined]
            logger.info("Removed capacity: %s/%s", gateway_id, model_id)

    def available(self, gateway_id: str, model_id: str) -> int:
        """Return available slots for a (gateway, model) pair.

        Capacity minus in_flight.  Returns 0 when the slot is unknown
        or fully occupied.  Used by the
        DecisionEngine during feasibility evaluation to determine T0/T1 tier.
        """
        slot = _Slot(gateway_id=gateway_id, model_id=model_id)
        return max(0, self._capacity.get(slot, 0) - self._in_flight.get(slot, 0))  # type: ignore[attr-defined]

    def get_slot_info(self, gateway_id: str, model_id: str) -> tuple[int, int, int]:
        """Return (available, in_flight, capacity) tuple for a (gateway, model) slot.

        Used by _try_immediate for ranking and by diagnostic endpoints.
        All values are 0 when the slot is unknown.
        """
        slot = _Slot(gateway_id=gateway_id, model_id=model_id)
        capacity = self._capacity.get(slot, 0)  # type: ignore[attr-defined]
        in_flight = self._in_flight.get(slot, 0)  # type: ignore[attr-defined]
        return max(0, capacity - in_flight), in_flight, capacity

    def get_available_gateways(self, model_id: str) -> list[tuple[str, int]]:
        """Return gateways with available capacity, sorted descending.

        Returns ``[(gateway_id, available)]`` for all gateways with
        available > 0.  Used by routing to enumerate candidates for a model.
        Only includes slots where at least one request can be admitted immediately.
        """
        available_pairs: list[tuple[str, int]] = []
        for slot in self._capacity:  # type: ignore[attr-defined]
            if slot.model_id != model_id:
                continue
            available = self.available(slot.gateway_id, slot.model_id)
            if available > 0:
                available_pairs.append((slot.gateway_id, available))
        return sorted(available_pairs, key=lambda x: x[1], reverse=True)
