"""Per-root admission caps for the charter runner.

The primary throttle is the bus-derived in-flight guard (one window per root
until a fresh CHECKPOINT lands). Caps are a safety backstop against runaway
auto-admission — set to very-long bounds per operator bind (2026-07-19). A root
that hits a worker failure/timeout is *stopped* (no re-admit) until a human
resets it; there is no auto-retry.

Admit counters are in-memory (manage restart resets admit counts). ``stopped_reason``
is durable under ``charter_runner_data_dir()`` (S2). Pre-fire intent markers are
markers are durable on disk so a crash between ``fire_window`` and the
admission pointer cannot re-dispatch the same (root, window) on restart
(A-R3-4). The bus in-flight guard remains authoritative once the pointer lands.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from libs.charter_runner_store.db import charter_runner_data_dir


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _default_intent_dir() -> Path:
    return Path.home() / ".local" / "share" / "charter-runner" / "admit-intent"


def _default_revise_dir() -> Path:
    return Path.home() / ".local" / "share" / "charter-runner" / "revise-count"


_REVISE_PICKUP_RE = re.compile(r"\brevise\b|\bG\d+[a-c]\b", re.IGNORECASE)
_STOP_DIR = "cap-stops"
_MALFORMED_STOP = "malformed_stop_state"


def _schedule_caps_cleared_emit(root_id: str) -> None:
    """Best-effort async emit when cap stop clears (§B3 manage.charter.caps.cleared)."""
    from scripts.model_manager.observation_event_charter import (
        emit_manage_charter_caps_cleared,
    )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(emit_manage_charter_caps_cleared(root=root_id))


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
        revise_dir: Path | None = None,
        revise_cap: int | None = None,
    ) -> None:
        self._caps = caps or WindowCaps.from_env()
        self._roots: dict[str, _RootState] = {}
        self._intent_dir = (
            intent_dir if intent_dir is not None else _default_intent_dir()
        )
        self._revise_dir = (
            revise_dir if revise_dir is not None else _default_revise_dir()
        )
        self._revise_cap = (
            revise_cap if revise_cap is not None else _env_int("CHARTER_REVISE_CAP", 3)
        )
        # Heal counts survive reset() so checkpoint_missing loops stay bounded.
        self._heal_counts: dict[str, int] = {}
        # Consult-stall generations are independent and monotonic across root resets.
        self._consult_stall_heals: dict[str, int] = {}
        self._stop_dir = charter_runner_data_dir() / _STOP_DIR
        self._load_stops()

    def _stop_path(self, root_id: str) -> Path:
        return self._stop_dir / f"{root_id}.json"

    def _load_stops(self) -> None:
        if not self._stop_dir.is_dir():
            return
        for path in self._stop_dir.glob("*.json"):
            root_id = path.stem
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._roots.setdefault(root_id, _RootState()).stopped_reason = _MALFORMED_STOP
                continue
            reason = raw.get("stopped_reason") if isinstance(raw, dict) else None
            if not isinstance(reason, str) or not reason.strip():
                self._roots.setdefault(root_id, _RootState()).stopped_reason = _MALFORMED_STOP
            else:
                self._roots.setdefault(root_id, _RootState()).stopped_reason = reason

    def _persist_stop(self, root_id: str, reason: str) -> None:
        self._stop_dir.mkdir(parents=True, exist_ok=True)
        path = self._stop_path(root_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"stopped_reason": reason}, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def _clear_stop(self, root_id: str) -> None:
        self._stop_path(root_id).unlink(missing_ok=True)

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
        self._persist_stop(root_id, reason)

    def reset(self, root_id: str) -> bool:
        """Clear in-memory + durable stop state. Returns True when a stop was cleared."""
        had_stop = False
        state = self._roots.get(root_id)
        if state is not None and state.stopped_reason is not None:
            had_stop = True
        if self._stop_path(root_id).is_file():
            had_stop = True
        self._roots.pop(root_id, None)
        self._clear_stop(root_id)
        if had_stop:
            _schedule_caps_cleared_emit(root_id)
        return had_stop

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

    def intent_worker_thread(self, root_id: str, window_index: int) -> str | None:
        """Optional ``worker=`` line on the durable intent file (post-fire bind)."""
        path = self.intent_path(root_id, window_index)
        if not path.is_file():
            return None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines[1:]:
            if line.startswith("worker="):
                val = line.split("=", 1)[1].strip()
                return val or None
        return None

    def bind_intent_worker(
        self, root_id: str, window_index: int, worker_thread: str
    ) -> None:
        """Append worker thread to intent after dispatch fire (orphan-heal probe)."""
        if not worker_thread:
            return
        path = self.intent_path(root_id, window_index)
        if not path.is_file():
            return
        marker = f"worker={worker_thread}"
        try:
            text = path.read_text(encoding="utf-8").rstrip()
        except OSError:
            return
        if marker in text:
            return
        path.write_text(text + f"\n{marker}\n", encoding="utf-8")

    def resolve_orphan_worker_thread(
        self, root_id: str, window_index: int
    ) -> str | None:
        """Worker id from intent sidecar or root transcript index, if recorded."""
        worker = self.intent_worker_thread(root_id, window_index)
        if worker:
            return worker
        from .. import window_log

        index_path = window_log.root_index_path(root_id)
        if not index_path.is_file():
            return None
        needle = f"ADMIT window={window_index}"
        try:
            lines = index_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            if needle not in line:
                continue
            match = re.search(r"agent-bus:(\S+)", line)
            if match:
                return match.group(1)
        return None

    def revise_path(self, root_id: str) -> Path:
        return self._revise_dir / f"{root_id}.revise"

    def get_revise_count(self, root_id: str) -> int:
        path = self.revise_path(root_id)
        if not path.exists():
            return 0
        try:
            return max(0, int(path.read_text(encoding="utf-8").strip().splitlines()[0]))
        except (OSError, ValueError, IndexError):
            return 0

    def increment_revise(self, root_id: str) -> int:
        """Bump the durable revise counter; return the new count."""
        self._revise_dir.mkdir(parents=True, exist_ok=True)
        count = self.get_revise_count(root_id) + 1
        self.revise_path(root_id).write_text(f"{count}\n", encoding="utf-8")
        return count

    def reset_revise(self, root_id: str) -> None:
        self.revise_path(root_id).unlink(missing_ok=True)

    def increment_heal(self, root_id: str) -> int:
        """Bump the in-memory heal counter (survives ``reset``); return new count.

        Distinct from revise_cap — checkpoint_missing is not a deploy-verify probe
        failure and must not steal G4a/b/c budget (R-admit A4).
        """
        count = self._heal_counts.get(root_id, 0) + 1
        self._heal_counts[root_id] = count
        return count

    def get_heal_count(self, root_id: str) -> int:
        return self._heal_counts.get(root_id, 0)

    def increment_consult_stall_heal(self, root_id: str) -> int:
        """Bump the consult-stall generation without consuming self-heal budget."""
        count = self._consult_stall_heals.get(root_id, 0) + 1
        self._consult_stall_heals[root_id] = count
        return count

    def get_consult_stall_heal_count(self, root_id: str) -> int:
        return self._consult_stall_heals.get(root_id, 0)

    @property
    def revise_cap(self) -> int:
        return self._revise_cap

    def check_revise_admit(
        self, root_id: str, next_pickup: list[str]
    ) -> tuple[bool, str | None]:
        """Block admission when a revise pickup would exceed the machine cap."""
        if not any(_REVISE_PICKUP_RE.search(item) for item in next_pickup):
            return True, None
        count = self.get_revise_count(root_id)
        if count >= self._revise_cap:
            return False, "revise_cap_exhausted"
        return True, None

    def _recent_count(self, state: _RootState, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        cutoff = now - 3600.0
        return sum(1 for ts in state.admits if ts >= cutoff)
