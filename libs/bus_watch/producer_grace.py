"""Wall-clock grace tracker for in-flight producer-link suppression."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ProducerGrace:
    """Expire suppression when producer fingerprint and turn_count stall."""

    def __init__(
        self,
        *,
        grace_seconds: float,
        now_fn: Callable[[], float],
    ) -> None:
        self._grace_seconds = max(0.0, float(grace_seconds))
        self._now_fn = now_fn
        self._fingerprint: tuple[Any, ...] | None = None
        self._started_at: float | None = None

    @staticmethod
    def _fingerprint_for(producer: dict[str, Any], turn_count: int) -> tuple[Any, ...]:
        return (
            producer.get("state"),
            producer.get("terminal_status"),
            producer.get("delivery_at"),
            producer.get("linked_at"),
            turn_count,
        )

    def observe(self, producer: dict[str, Any], turn_count: int) -> bool:
        """Return True when grace has expired for the current fingerprint."""
        fp = self._fingerprint_for(producer, turn_count)
        now = self._now_fn()
        if fp != self._fingerprint:
            self._fingerprint = fp
            self._started_at = now
            return False
        if self._started_at is None:
            self._started_at = now
            return False
        return (now - self._started_at) >= self._grace_seconds
