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
from .checkpoint_parse import parse_checkpoint
from .eligibility import next_window_index as bus_next_window_index
from .pickup_advance import gated_pickup_from_parsed
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


def release_window_on_harvest(
    root_id: str,
    window_index: int,
    checkpoint_body: str,
) -> bool:
    """Apply ``HARVEST_OK``: record the window, clear WIP, advance the pickup.

    Returns False when the root has no ledger row (unseeded roots harvest without
    a ledger). Idempotent — a repeat call for the same window writes the same
    values, so the durable harvested marker and this release cannot disagree.
    """
    conn = open_default_ledger()
    try:
        row = load_root(conn, root_id)
        if row is None:
            return False
        live = gated_pickup_from_parsed(parse_checkpoint(checkpoint_body or ""))
        released = replace(
            row,
            status=RootStatus.IDLE,
            pickup_gid=live.gid if live is not None else row.pickup_gid,
            wip_window_id=None,
            last_window_id=window_id_for(root_id, window_index),
            last_transition=Transition.HARVEST_OK.value,
            consult_role=None,
            consult_attempts=0,
            consult_next_retry=None,
            updated_at=time.time(),
        )
        upsert_root(conn, released)
        write_cortex_mirror(released)
        logger.info(
            "charter-runner ledger release root=%s window=%s pickup=%s (was %s)",
            root_id,
            window_index,
            released.pickup_gid,
            row.pickup_gid,
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
