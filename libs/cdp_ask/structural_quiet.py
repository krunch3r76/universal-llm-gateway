"""Structural-quiet failsafe for latched streaming/stop harvest wedges (arc 5996 leg 2)."""

from __future__ import annotations

from dataclasses import dataclass

STRUCTURAL_QUIET_SAMPLES = 240
"""Consecutive harvest samples with unchanged ``n``/``body_len`` and idle task map (~120s at 500ms poll)."""


@dataclass
class StructuralQuietTracker:
    """Track consecutive structural-quiet samples for Tier A / Tier B failsafe."""

    streak: int = 0
    _last_n: int | None = None
    _last_body_len: int | None = None

    def observe(self, state: dict) -> None:
        """Update streak from one harvest sample. ``task_map_working`` vetoes and resets."""
        n = int(state.get("n") or 0)
        body_len = int(state.get("body_len") or 0)

        if state.get("task_map_working"):
            self.streak = 0
        elif (
            state.get("task_map_present")
            and state.get("task_map_idle")
            and not state.get("task_map_working")
            and self._last_n is not None
            and self._last_body_len is not None
            and n == self._last_n
            and body_len == self._last_body_len
        ):
            self.streak += 1
        else:
            self.streak = 0

        self._last_n = n
        self._last_body_len = body_len

    @property
    def quiet_satisfied(self) -> bool:
        return self.streak >= STRUCTURAL_QUIET_SAMPLES
