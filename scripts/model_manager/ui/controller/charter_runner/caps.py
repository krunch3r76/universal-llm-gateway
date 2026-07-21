"""Per-root admission caps for the charter runner.

The primary throttle is the bus-derived in-flight guard (one window per root
until a fresh CHECKPOINT lands). Caps are a safety backstop against runaway
auto-admission — set to very-long bounds per operator bind (2026-07-19). A root
that hits a worker failure/timeout is *stopped* (no re-admit) until a human
resets it; there is no auto-retry.

Admit counters are in-memory (manage restart resets them). Pre-fire intent
markers are durable on disk so a crash between ``fire_window`` and the
admission pointer cannot re-dispatch the same (root, window) on restart
(A-R3-4). The bus in-flight guard remains authoritative once the pointer lands.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _default_intent_dir() -> Path:
    return Path.home() / ".local" / "share" / "charter-runner" / "admit-intent"


@dataclass(frozen=True)
class WindowCaps:
    max_consecutive: int = 200
    max_per_hour: int = 30

    @classmethod
    def from_env(cls) -> WindowCaps:
        return cls(
            max_consecutive=_env_int("CHARTER_MAX_CONSECUTIVE_WINDOWS", 200),
            max_per_hour=_env_int("CHARTER_MAX_WINDOWS_PER_HOUR", 30),
        )


@dataclass
class _RootState:
    admits: list[float] = field(default_factory=list)  # unix ts of each admit
    consecutive: int = 0
    stopped_reason: str | None = None


class CapStore:
    """Tracks admission bookkeeping per root thread."""

    def __init__(
        self,
        caps: WindowCaps | None = None,
        *,
        intent_dir: Path | None = None,
    ) -> None:
        self._caps = caps or WindowCaps.from_env()
        self._roots: dict[str, _RootState] = {}
        self._intent_dir = intent_dir if intent_dir is not None else _default_intent_dir()

    def check(
        self, root_id: str, *, now: float | None = None
    ) -> tuple[bool, str | None]:
        """Return (allowed, skip_reason). Does not mutate state."""
        state = self._roots.get(root_id)
        if state is None:
            return True, None
        if state.stopped_reason is not None:
            return False, f"stopped:{state.stopped_reason}"
        if state.consecutive >= self._caps.max_consecutive:
            return False, "cap_consecutive"
        if self._recent_count(state, now=now) >= self._caps.max_per_hour:
            return False, "cap_per_hour"
        return True, None

    def record_admit(self, root_id: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        state = self._roots.setdefault(root_id, _RootState())
        state.admits.append(now)
        state.consecutive += 1

    def mark_failed(self, root_id: str, reason: str) -> None:
        state = self._roots.setdefault(root_id, _RootState())
        state.stopped_reason = reason

    def reset(self, root_id: str) -> None:
        self._roots.pop(root_id, None)

    def intent_path(self, root_id: str, window_index: int) -> Path:
        return self._intent_dir / f"{root_id}-w{window_index}.intent"

    def has_admit_intent(self, root_id: str, window_index: int) -> bool:
        return self.intent_path(root_id, window_index).exists()

    def mark_admit_intent(self, root_id: str, window_index: int) -> None:
        """Durable pre-fire marker keyed (root, window) — crash-safe vs double-fire."""
        self._intent_dir.mkdir(parents=True, exist_ok=True)
        self.intent_path(root_id, window_index).write_text(
            f"{time.time():.3f}\n", encoding="utf-8"
        )

    def clear_admit_intent(self, root_id: str, window_index: int) -> None:
        self.intent_path(root_id, window_index).unlink(missing_ok=True)

    def _recent_count(self, state: _RootState, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        cutoff = now - 3600.0
        return sum(1 for ts in state.admits if ts >= cutoff)
