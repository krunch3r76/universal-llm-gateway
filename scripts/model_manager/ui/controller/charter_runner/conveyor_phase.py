"""Structured conveyor dormancy — phase transitions and D5 append cursor."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Literal

from .checkpoint_schema import ParsedCheckpoint, parse_checkpoint
from .pickup_advance import gated_pickup_from_parsed, gid_of_row, worker_substrate_compatible
from .root_ledger import (
    RootLedgerRow,
    load_root,
    upsert_root,
    write_cortex_mirror,
)

ConveyorPhase = Literal["dormant", "active"]


def pickup_append_is_fresh(
    *, append_high_water: int, last_admit_cursor: int
) -> bool:
    """D5: fresh enroll delta iff structured append high-water exceeds last-admit cursor."""
    return append_high_water > last_admit_cursor


def structured_pickup_append_high_water(parsed: ParsedCheckpoint | None) -> int:
    """Monotone per-root append high-water — count of structured Next-pickup rows."""
    if parsed is None:
        return 0
    return len(parsed.next_pickup)


def open_gated_g_rows(parsed: ParsedCheckpoint) -> list[str]:
    """Structured gated Next-pickup rows carrying G ids (window-close fact)."""
    from .checkpoint_schema import item_is_gated

    rows: list[str] = []
    for row in parsed.next_pickup:
        if not item_is_gated(row):
            continue
        gid = gid_of_row(row)
        if gid is not None and gid.upper().startswith("G"):
            rows.append(row)
    return rows


def _persist_row(conn, row: RootLedgerRow) -> RootLedgerRow:
    upsert_root(conn, row)
    write_cortex_mirror(row)
    return row


def set_conveyor_phase(
    conn,
    root_id: str,
    phase: ConveyorPhase,
    *,
    pickup_append_cursor: int | None = None,
) -> RootLedgerRow | None:
    existing = load_root(conn, root_id)
    if existing is None:
        return None
    updated = replace(
        existing,
        conveyor_phase=phase,
        pickup_append_cursor=(
            pickup_append_cursor
            if pickup_append_cursor is not None
            else existing.pickup_append_cursor
        ),
        updated_at=time.time(),
    )
    return _persist_row(conn, updated)


def bootstrap_seed_pickup_matches_tip(
    row: RootLedgerRow, parsed: ParsedCheckpoint | None
) -> bool:
    """§7.2(iv): seeded birth G-row registers as the initial enroll_rows delta."""
    if row.conveyor_phase != "dormant":
        return False
    if row.last_window_id is not None:
        return False
    if not row.pickup_gid or not row.pickup_executor:
        return False
    live = gated_pickup_from_parsed(parsed)
    if live is None:
        return False
    if live.gid.upper() != row.pickup_gid.upper():
        return False
    if not live.executor or not worker_substrate_compatible(live.executor):
        return False
    return True


def conveyor_wake_is_due(
    row: RootLedgerRow, parsed: ParsedCheckpoint | None
) -> bool:
    """Fresh append delta or §7.2(iv) bootstrap seed pickup."""
    high_water = structured_pickup_append_high_water(parsed)
    if pickup_append_is_fresh(
        append_high_water=high_water,
        last_admit_cursor=row.pickup_append_cursor,
    ):
        return True
    return bootstrap_seed_pickup_matches_tip(row, parsed)


def wake_conveyor_if_fresh_append(
    conn, row: RootLedgerRow, parsed: ParsedCheckpoint | None
) -> RootLedgerRow:
    if row.conveyor_phase != "dormant":
        return row
    if not conveyor_wake_is_due(row, parsed):
        return row
    woken = set_conveyor_phase(conn, row.root_id, "active")
    return woken if woken is not None else row


def record_admit_cursor(
    conn, row: RootLedgerRow, parsed: ParsedCheckpoint | None
) -> RootLedgerRow:
    high_water = structured_pickup_append_high_water(parsed)
    if high_water <= row.pickup_append_cursor:
        return row
    updated = replace(
        row,
        pickup_append_cursor=high_water,
        updated_at=time.time(),
    )
    return _persist_row(conn, updated)


def maybe_set_dormant_on_window_close(
    conn, root_id: str, checkpoint_body: str
) -> RootLedgerRow | None:
    """Window close with zero OPEN G-rows ⇒ conveyor_phase dormant (structured facts)."""
    try:
        parsed = parse_checkpoint(checkpoint_body or "")
    except Exception:  # noqa: BLE001 — never abort harvest
        return None
    if open_gated_g_rows(parsed):
        return None
    high_water = structured_pickup_append_high_water(parsed)
    return set_conveyor_phase(
        conn,
        root_id,
        "dormant",
        pickup_append_cursor=high_water,
    )


__all__ = [
    "ConveyorPhase",
    "bootstrap_seed_pickup_matches_tip",
    "conveyor_wake_is_due",
    "maybe_set_dormant_on_window_close",
    "open_gated_g_rows",
    "pickup_append_is_fresh",
    "record_admit_cursor",
    "set_conveyor_phase",
    "structured_pickup_append_high_water",
    "wake_conveyor_if_fresh_append",
]
