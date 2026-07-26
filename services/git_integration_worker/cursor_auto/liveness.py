"""In-process Auto handler registration + heartbeat for arm predicate.

``handler_status=auto-admit-armed`` requires a live registered handler — a
successful turn write alone is not arm evidence (R-admit HIGH / F1).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AutoLivenessRegistry:
    """Process-local registry of live Auto handlers for ``lane:cursor-auto``."""

    heartbeat_ttl_s: float = 30.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _handlers: dict[str, float] = field(default_factory=dict)
    _started_at: float = field(default_factory=time.monotonic)

    def register(self, handler_id: str) -> None:
        """Register or refresh a live Auto handler heartbeat."""
        with self._lock:
            self._handlers[handler_id] = time.monotonic()

    def heartbeat(self, handler_id: str) -> bool:
        """Refresh heartbeat; re-register if pruned while a long job held the loop.

        Mid-job silence can exceed ``heartbeat_ttl_s``; ``is_live``/snapshot prune
        the id. A strict miss-return here left the lane permanently dead after the
        first nested SDK job (5867 DIRECTIVE-4 / dead-handler friction).
        """
        with self._lock:
            existed = handler_id in self._handlers
            self._handlers[handler_id] = time.monotonic()
            return existed

    def unregister(self, handler_id: str) -> None:
        with self._lock:
            self._handlers.pop(handler_id, None)

    def _prune_locked(self, now: float) -> None:
        stale = [
            hid
            for hid, ts in self._handlers.items()
            if (now - ts) > self.heartbeat_ttl_s
        ]
        for hid in stale:
            del self._handlers[hid]

    def is_live(self) -> bool:
        """True when ≥1 handler has a fresh heartbeat within TTL."""
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            return bool(self._handlers)

    def snapshot(self) -> dict[str, Any]:
        """Liveness snapshot for `/cursor-auto/liveness` and arm probes."""
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            handlers = {
                hid: {"age_s": round(now - ts, 3)}
                for hid, ts in self._handlers.items()
            }
        return {
            "live": bool(handlers),
            "lane": "cursor-auto",
            "handler_count": len(handlers),
            "handlers": handlers,
            "heartbeat_ttl_s": self.heartbeat_ttl_s,
            "uptime_s": round(now - self._started_at, 3),
        }


_REGISTRY = AutoLivenessRegistry()


def get_registry() -> AutoLivenessRegistry:
    """Return the process-global Auto liveness registry."""
    return _REGISTRY
