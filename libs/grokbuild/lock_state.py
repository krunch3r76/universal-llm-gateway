"""Reader/writer lock state for grokbuild cwd registry."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Holder:
    dispatch_id: str
    mode: str  # "read_only" | "edit"
    pid: int | None = None  # subprocess pid; backfilled by the runner after spawn
    acquired_at: float = field(default_factory=time.monotonic)


@dataclass
class _LockState:
    writer: _Holder | None = None
    readers: dict[str, _Holder] = field(default_factory=dict)  # dispatch_id -> holder

    def is_empty(self) -> bool:
        return self.writer is None and not self.readers
