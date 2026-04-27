"""Mixin: diagnostic queries (admission state snapshot, full pool snapshot)."""

from __future__ import annotations

import time
from typing import Any


class _DiagnosticsMixin:
    def get_admission_state(self, model_id: str) -> dict[str, object]:
        """Return admission snapshot for one model_id.

        Used by GET /api/v1/admission/state for RAG startup snapshot.

        Returns:
            paused: True iff admission is currently suspended.
            paused_reason: the active pause reason string, or None.
            paused_until_ms: estimated Unix-ms deadline, or None.
            queue_depth: number of requests queued waiting for a slot.

        Note: paused_until_ms is derived by combining the monotonic deadline
        with the current wall-clock time. It is an approximation — sub-second
        drift is acceptable for a coordination hint.
        """
        now_mono = time.monotonic()
        now_wall = time.time()
        deadline = self._paused_until.get(model_id)  # type: ignore[attr-defined]
        if deadline is not None and deadline > now_mono:
            remaining_s = deadline - now_mono
            paused: bool = True
            paused_reason: str | None = self._paused_reason.get(model_id)  # type: ignore[attr-defined]
            paused_until_ms: int | None = int((now_wall + remaining_s) * 1000)
        else:
            paused = False
            paused_reason = None
            paused_until_ms = None
        queue = self._queues.get(model_id)  # type: ignore[attr-defined]
        return {
            "paused": paused,
            "paused_reason": paused_reason,
            "paused_until_ms": paused_until_ms,
            "queue_depth": len(queue) if queue else 0,
        }

    def get_snapshot(self) -> dict[str, Any]:
        """Return a diagnostic snapshot of all capacity state as a plain dict.

        Includes per-slot capacity/in_flight, per-model queue contents with
        waiter request_ids and allowed gateways, and aggregate totals.  Used
        by health endpoints, logging, and the MCP manage status command.
        """
        now = time.monotonic()
        paused = {
            mid: {
                "remaining_s": max(0.0, deadline - now),
                "reason": self._paused_reason.get(mid, ""),  # type: ignore[attr-defined]
            }
            for mid, deadline in self._paused_until.items()  # type: ignore[attr-defined]
            if deadline > now
        }
        return {
            "capacity": {str(s): c for s, c in self._capacity.items()},  # type: ignore[attr-defined]
            "in_flight": {str(s): c for s, c in self._in_flight.items()},  # type: ignore[attr-defined]
            "queues": {
                mid: [
                    {
                        "request_id": w.request_id,
                        "allowed_gateways": list(w.allowed_gateway_ids),
                        "done": w.future.done(),
                    }
                    for w in q
                ]
                for mid, q in self._queues.items()  # type: ignore[attr-defined]
            },
            "paused_admission": paused,
            "total_capacity": sum(self._capacity.values()),  # type: ignore[attr-defined]
            "total_in_flight": sum(self._in_flight.values()),  # type: ignore[attr-defined]
            "total_queued": sum(len(q) for q in self._queues.values()),  # type: ignore[attr-defined]
        }
