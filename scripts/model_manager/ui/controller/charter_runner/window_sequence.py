"""Window numbering and the ``HARVEST_OK`` release of a closed window.

Two holes this closes, both observed on root 5975 (a:26628):

1. **Numbering had a single source.** ``window_index`` was purely
   ``count_admissions(bus_turns) + 1``, so any tick whose view of the root's
   admission pointers came back short restarted numbering at 1 — 5975 went
   ``13 → 1`` and then re-fired ``5975-w1.md`` eight times. The index is now the
   max of three independent records (bus pointers, ledger ``last_window_id``,
   local transcript index), which cannot regress while any one of them survives.

2. **Nothing released the ledger WIP.** ``Transition.HARVEST_OK`` existed but was
   never applied: ``wip_window_id`` was only ever set, so a root froze in
   ``ADMITTED`` after its first window and ``decide`` returned ``NOOP`` forever
   (live on roots 6091 and 6110 before this landed). Harvest now closes the
   transition it always named.
"""

from __future__ import annotations

import time
from dataclasses import replace

from universal_logging import get_logger

from . import window_log
from .admission import next_window_index as bus_next_window_index
from .root_ledger import (
    RootLedgerRow,
    RootStatus,
    Transition,
    load_root,
    open_default_ledger,
    upsert_root,
    write_cortex_mirror,
)

logger = get_logger(__name__)


def window_id_for(root_id: str, window_index: int) -> str:
    """Canonical window id — matches the packet footer's ``window_id``."""
    return f"charter-{root_id}-w{window_index}"


def window_index_from_id(window_id: str | None) -> int:
    """Trailing ``-w{n}`` of a window id; 0 when absent or malformed."""
    raw = str(window_id or "")
    _, _, tail = raw.rpartition("-w")
    if not tail or not tail.isdigit():
        return 0
    return int(tail)


def next_window_index(
    root_id: str,
    turns: list[dict],
    *,
    row: RootLedgerRow | None = None,
) -> int:
    """Next window index — monotonic across bus, ledger, and transcript records.

    Each source can be individually incomplete: bus turns can come back short,
    the ledger starts empty for a root admitted before it was seeded, and the
    transcript lives in ``/tmp`` and does not survive a reboot. Taking the max
    means a window index is only reused if *every* record of it is gone.
    """
    bus_high = max(bus_next_window_index(turns) - 1, 0)
    ledger_high = window_index_from_id(row.last_window_id if row else None)
    log_high = window_log.max_window_index(root_id)
    return max(bus_high, ledger_high, log_high) + 1


def clear_uncorrelatable_wip(conn, row: RootLedgerRow) -> RootLedgerRow:
    """Drop a ``wip_window_id`` that names no window index; return the live row.

    Admits before this module wrote a bare ``charter-{root}-w`` stub with the index
    omitted. Release keys on a window index, so a stub can never be released: the root
    stays ``ADMITTED`` and ``decide`` returns ``NOOP`` forever — live on 6091 and 6110.
    A wip that cannot be correlated with any admission pointer is not in-flight state,
    and the bus tip (``has_wip``) still guards against admitting over a real window.
    """
    if not row.wip_window_id or window_index_from_id(row.wip_window_id) > 0:
        return row
    cleared = replace(
        row,
        status=RootStatus.IDLE if row.status == RootStatus.ADMITTED else row.status,
        wip_window_id=None,
        updated_at=time.time(),
    )
    upsert_root(conn, cleared)
    write_cortex_mirror(cleared)
    logger.warning(
        "charter-runner cleared uncorrelatable wip root=%s wip=%r",
        row.root_id,
        row.wip_window_id,
    )
    return cleared


def release_window_on_harvest(root_id: str, window_index: int) -> bool:
    """Apply ``HARVEST_OK``: record the closed window and clear its WIP.

    Deliberately leaves ``pickup_gid`` alone. ``kernel_tick`` advances the pickup
    from the **tip** before every ``decide``, and harvest runs earlier in the same
    tick, so the advance happens regardless — with the tip as its single source.
    Advancing here as well made the *harvested* window's CHECKPOINT a second
    source: right for a window that just closed, since its terminal is the tip,
    but stale for a backlog window, where it walked root 5975's pickup backwards
    onto a gid the tip had long left behind.

    Returns False when there is nothing to release — an unseeded root, or a window
    already recorded with its WIP clear. Restating ``IDLE`` on a re-harvest is not
    idempotent in effect: it flipped 5975 out of ``CONSULT_QUEUED`` on every pass
    of the re-harvest loop (a:26582), re-arming a transition that had settled.
    """
    conn = open_default_ledger()
    try:
        row = load_root(conn, root_id)
        if row is None:
            return False
        window_id = window_id_for(root_id, window_index)
        holds_wip = row.wip_window_id == window_id
        if not holds_wip and window_index_from_id(row.last_window_id) >= window_index:
            return False
        released = replace(
            row,
            status=RootStatus.IDLE if holds_wip else row.status,
            wip_window_id=None if holds_wip else row.wip_window_id,
            last_window_id=window_id,
            last_transition=Transition.HARVEST_OK.value,
            updated_at=time.time(),
        )
        upsert_root(conn, released)
        write_cortex_mirror(released)
        logger.info(
            "charter-runner ledger release root=%s window=%s wip_held=%s",
            root_id,
            window_index,
            holds_wip,
        )
        return True
    finally:
        conn.close()


__all__ = [
    "clear_uncorrelatable_wip",
    "next_window_index",
    "release_window_on_harvest",
    "window_id_for",
    "window_index_from_id",
]
