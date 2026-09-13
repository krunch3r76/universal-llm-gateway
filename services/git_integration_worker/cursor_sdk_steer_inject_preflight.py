"""Fail-closed inject preflight — 404 unknown, 409 not-live before deposit."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from services.git_integration_worker.cursor_sdk_park_preflight import (
    _TERMINAL_ROW_STATUSES,
    load_park_candidate_row,
)
from services.git_integration_worker.cursor_sdk_supersede import is_dispatch_live


class InjectRefusal(StrEnum):
    """Inject refusals in precedence order."""

    NOT_FOUND = "NOT_FOUND"
    NOT_LIVE = "NOT_LIVE"


REFUSAL_HTTP: dict[InjectRefusal, tuple[int, bool]] = {
    InjectRefusal.NOT_FOUND: (404, False),
    InjectRefusal.NOT_LIVE: (409, False),
}


@dataclass(frozen=True, slots=True)
class InjectPreflight:
    refusal: InjectRefusal | None
    row: dict[str, Any] | None
    detail: str | None = None


def preflight_inject(dispatch_id: str) -> InjectPreflight:
    """Decide inject refusal for *dispatch_id* without mutating state."""
    row = load_park_candidate_row(dispatch_id)
    if row is None:
        return InjectPreflight(refusal=InjectRefusal.NOT_FOUND, row=None)
    status = str(row.get("status") or "")
    if status in _TERMINAL_ROW_STATUSES:
        return InjectPreflight(
            refusal=InjectRefusal.NOT_LIVE,
            row=row,
            detail=f"row status={status!r}",
        )
    if not is_dispatch_live(dispatch_id=dispatch_id):
        return InjectPreflight(
            refusal=InjectRefusal.NOT_LIVE,
            row=row,
            detail="no live bridge run registered in this process",
        )
    thread_id = row.get("thread_id")
    if not thread_id:
        return InjectPreflight(
            refusal=InjectRefusal.NOT_LIVE,
            row=row,
            detail="thread_id not yet recorded",
        )
    return InjectPreflight(refusal=None, row=row)


__all__ = [
    "InjectPreflight",
    "InjectRefusal",
    "REFUSAL_HTTP",
    "preflight_inject",
]
