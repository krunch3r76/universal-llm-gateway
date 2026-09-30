"""Fail-closed inject preflight — 404 unknown, 409 not-live before deposit."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_park_preflight import (
    _TERMINAL_ROW_STATUSES,
    load_park_candidate_row,
)
from services.git_integration_worker.cursor_sdk_supersede import is_dispatch_live

_CURSOR_SDK_DISPATCH_PREFIX = "cursor-sdk:dispatch:"


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


def _preflight_one_row(row: dict[str, Any]) -> InjectPreflight:
    status = str(row.get("status") or "")
    if status in _TERMINAL_ROW_STATUSES:
        return InjectPreflight(
            refusal=InjectRefusal.NOT_LIVE,
            row=row,
            detail=f"row status={status!r}",
        )
    dispatch_key = str(row["dispatch_id"])
    if not is_dispatch_live(dispatch_id=dispatch_key):
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


def _execution_id_candidates(execution_id: str) -> list[dict[str, Any]]:
    conn = CursorDispatchLedger.instance()._connect()
    cur = conn.execute(
        "SELECT * FROM cursor_sdk_dispatches WHERE execution_id=?",
        (execution_id,),
    )
    return [{k: row[k] for k in row.keys()} for row in cur.fetchall()]


def preflight_inject(submitted_id: str) -> InjectPreflight:
    """Decide inject refusal for *submitted_id* without mutating state."""
    stripped = submitted_id.removeprefix(_CURSOR_SDK_DISPATCH_PREFIX)
    row = load_park_candidate_row(stripped)
    if row is not None:
        return _preflight_one_row(row)
    candidates = _execution_id_candidates(stripped)
    if not candidates:
        return InjectPreflight(refusal=InjectRefusal.NOT_FOUND, row=None)
    passed: list[dict[str, Any]] = []
    failed: list[InjectPreflight] = []
    for candidate in candidates:
        checked = _preflight_one_row(candidate)
        if checked.refusal is None:
            passed.append(candidate)
        else:
            failed.append(checked)
    if len(passed) == 1:
        return InjectPreflight(refusal=None, row=passed[0])
    if len(passed) > 1:
        n = len(passed)
        return InjectPreflight(
            refusal=InjectRefusal.NOT_LIVE,
            row=None,
            detail=f"ambiguous execution_id: {n} live rows share this execution_id",
        )
    detail = (
        failed[0].detail
        if len(candidates) == 1
        else "no live row for this execution_id"
    )
    return InjectPreflight(refusal=InjectRefusal.NOT_LIVE, row=None, detail=detail)


__all__ = [
    "InjectPreflight",
    "InjectRefusal",
    "REFUSAL_HTTP",
    "preflight_inject",
]
