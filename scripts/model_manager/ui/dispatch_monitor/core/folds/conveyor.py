"""ConveyorFold — friction-belt enrollments from ``manage.charter.conveyor.*``.

Standing friction conveyor root is **6110** only. Charter root 6171 is a separate
enrolled seed (post-6110 rewrite), not a second friction belt.
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import signals
from ..protocols import EventRecord

#: Sole friction-belt conveyor identity.
FRICTION_BELT_ROOTS = frozenset({"6110"})


class ConveyorItemState:
    """Mutable per-friction conveyor enrollment."""

    __slots__ = (
        "friction_id",
        "todo_slug",
        "root_id",
        "conveyor_root",
        "state",
        "ticks_idle",
        "enrolled_ms",
        "last_signal_ms",
    )

    def __init__(self, friction_id: int) -> None:
        self.friction_id = friction_id
        self.todo_slug: str | None = None
        self.root_id: str | None = None
        self.conveyor_root: str | None = None
        self.state = "enrolled"
        self.ticks_idle: int | None = None
        self.enrolled_ms: int | None = None
        self.last_signal_ms: int | None = None


class ConveyorFold:
    """Accumulates friction-belt conveyor enrollments."""

    def __init__(self) -> None:
        self.items: dict[int, ConveyorItemState] = {}
        self.last_enroll_failed: dict[str, Any] | None = None

    def handlers(self) -> dict[str, Any]:
        return {
            signals.CHARTER_CONVEYOR_ENROLLED: self._on_enrolled,
            signals.CHARTER_CONVEYOR_STALE: self._on_stale,
            signals.CHARTER_CONVEYOR_DISENROLLED: self._on_disenrolled,
            signals.CHARTER_CONVEYOR_ENROLL_FAILED: self._on_enroll_failed,
        }

    def _on_enrolled(self, record: EventRecord) -> None:
        payload = record.payload
        friction_id = _friction_id(payload)
        if friction_id is None:
            return
        conveyor_root = _str(payload.get("conveyor_root")) or _str(payload.get("root"))
        if conveyor_root and conveyor_root not in FRICTION_BELT_ROOTS:
            return
        row = self.items.get(friction_id)
        if row is None:
            row = ConveyorItemState(friction_id)
            self.items[friction_id] = row
        row.state = "enrolled"
        row.ticks_idle = None
        row.todo_slug = _str(payload.get("todo_slug")) or row.todo_slug
        row.root_id = _str(payload.get("root")) or row.root_id
        row.conveyor_root = conveyor_root or row.conveyor_root
        if row.enrolled_ms is None:
            row.enrolled_ms = record.ts_unix_ms
        row.last_signal_ms = record.ts_unix_ms

    def _on_stale(self, record: EventRecord) -> None:
        payload = record.payload
        friction_id = _friction_id(payload)
        if friction_id is None:
            return
        root = _str(payload.get("root"))
        row = self.items.get(friction_id)
        if row is None:
            # Stale without prior enrolled in the seed window — still surface.
            if root and root not in FRICTION_BELT_ROOTS:
                return
            row = ConveyorItemState(friction_id)
            self.items[friction_id] = row
            row.conveyor_root = root
            row.root_id = root
        elif row.conveyor_root and row.conveyor_root not in FRICTION_BELT_ROOTS:
            return
        row.state = "stale"
        row.todo_slug = _str(payload.get("todo_slug")) or row.todo_slug
        row.root_id = root or row.root_id
        ticks = payload.get("ticks_idle")
        if isinstance(ticks, int) and not isinstance(ticks, bool):
            row.ticks_idle = ticks
        row.last_signal_ms = record.ts_unix_ms

    def _on_disenrolled(self, record: EventRecord) -> None:
        """Belt exit — enrollment removed from conveyor SoT."""
        payload = record.payload
        friction_id = _friction_id(payload)
        if friction_id is None:
            return
        root = _str(payload.get("root"))
        row = self.items.get(friction_id)
        if row is None:
            if root and root not in FRICTION_BELT_ROOTS:
                return
            row = ConveyorItemState(friction_id)
            self.items[friction_id] = row
            row.conveyor_root = root
            row.root_id = root
        elif row.conveyor_root and row.conveyor_root not in FRICTION_BELT_ROOTS:
            return
        row.state = "disenrolled"
        row.todo_slug = _str(payload.get("todo_slug")) or row.todo_slug
        row.root_id = root or row.root_id
        row.last_signal_ms = record.ts_unix_ms

    def _on_enroll_failed(self, record: EventRecord) -> None:
        """Record harvest→conveyor enroll failure (no row; attention may use later)."""
        payload = record.payload
        self.last_enroll_failed = {
            "root": _str(payload.get("root")),
            "window_index": payload.get("window_index"),
            "error": _str(payload.get("error")),
            "minted_count": payload.get("minted_count"),
            "ts_unix_ms": record.ts_unix_ms,
        }


def _friction_id(payload: Mapping[str, Any]) -> int | None:
    value = payload.get("friction_id")
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
