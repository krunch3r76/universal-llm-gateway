"""MCP caller-provenance stamp/join — kept out of ``sdk.py`` for the SLOC budget."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocols import EventRecord
    from .sdk import SdkFold


def _caller_from_mcp(
    seat_class: str | None, surface: str | None
) -> tuple[str, str] | None:
    """Map MCP request metadata to ``(caller_from, caller_via)``."""
    if surface == "code" or seat_class == "cursor":
        return ("ide", "mcp")
    if surface == "life" or seat_class == "claude":
        return ("claude.ai", "mcp")
    return None


def _find_row_by_execution_id(fold: SdkFold, execution_id: str) -> object | None:
    """Look up an existing row by ``execution_id`` / aliases — never create."""
    resolved = fold._aliases.resolve(execution_id)
    row = fold.dispatches.get(resolved)
    if row is not None:
        return row
    for candidate in fold.dispatches.values():
        if candidate.dispatch_id == execution_id or candidate.dispatch_id.startswith(
            f"{execution_id}-"
        ):
            return candidate
    return None


def _stamp_caller(
    row: object,
    *,
    seat_class: str | None,
    surface: str | None,
    caller_from: str,
    caller_via: str,
) -> None:
    from .sdk_state import SdkState

    assert isinstance(row, SdkState)
    if row.caller_via == "mcp":
        return
    if row.caller_from is None:
        row.caller_from = caller_from
    if row.caller_via is None:
        row.caller_via = caller_via
    if row.mcp_seat_class is None and seat_class:
        row.mcp_seat_class = seat_class
    if row.mcp_surface is None and surface:
        row.mcp_surface = surface


def stash_or_stamp(fold: SdkFold, record: EventRecord) -> None:
    """Extract MCP metadata and stamp or stash until the SDK row exists."""
    payload = record.payload
    execution_id = payload.get("execution_id")
    if not execution_id:
        return
    execution_id = str(execution_id)
    seat_class = str(payload["seat_class"]) if payload.get("seat_class") else None
    surface = str(payload["surface"]) if payload.get("surface") else None
    caller = _caller_from_mcp(seat_class, surface)
    if caller is None:
        return
    caller_from, caller_via = caller
    row = _find_row_by_execution_id(fold, execution_id)
    if row is not None:
        _stamp_caller(
            row,
            seat_class=seat_class,
            surface=surface,
            caller_from=caller_from,
            caller_via=caller_via,
        )
    else:
        fold._pending_caller[execution_id] = (caller_from, caller_via)


def apply_pending_caller(fold: SdkFold, row: object) -> None:
    """Apply stashed MCP caller once a dispatch row resolves."""
    from .sdk_state import SdkState

    assert isinstance(row, SdkState)
    if row.caller_via == "mcp" or not fold._pending_caller:
        return
    for key, (caller_from, caller_via) in fold._pending_caller.items():
        if row.dispatch_id == key or row.dispatch_id.startswith(f"{key}-"):
            if row.caller_from is None:
                row.caller_from = caller_from
            if row.caller_via is None:
                row.caller_via = caller_via
            return
