"""Advance the durable ledger pickup to the live gated CHECKPOINT row.

``pickup_gid`` was effectively write-once: ``seed_from_confirm`` set it and
``kernel_tick._ledger_row_from_state`` copied it forward on every transition, so
no path ever read the tip ``## Next-pickup`` to move it. A seeded root therefore
re-admitted its seed gid forever (a:26628). This module makes the tip the
authority and the seed a bootstrap floor.

Scope bind: advance writes ``pickup_gid`` only. ``pickup_lane`` selects
consult-vs-worker inside ``admission.decide``, so deriving it from a row's
declared ``executor_lane`` would silently re-route lanes and bypass the R-admit
consult boundary; the declared lane is carried on ``LivePickup`` for telemetry
and is honoured at admit time by ``executor_routing`` instead.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace

from universal_logging import get_logger

from .checkpoint_schema import ParsedCheckpoint, item_is_gated
from .root_ledger import (
    RootLedgerRow,
    Transition,
    upsert_root,
    write_cortex_mirror,
)

logger = get_logger(__name__)

# Scoreboard G-rows and charter R-beats — same shape checkpoint_parse gates on.
_GID_RE = re.compile(r"\b([GR]\d+[a-z]?)\b")
_EXECUTOR_RE = re.compile(r"executor\s*=\s*([A-Za-z0-9._/\-]+)")
_EXECUTOR_LANE_RE = re.compile(r"executor_lane:\s*(implement|judgment)\b", re.IGNORECASE)


@dataclass(frozen=True)
class LivePickup:
    """The tip's live gated pickup — what the next window is actually for."""

    gid: str
    row: str
    lane: str | None = None
    executor: str | None = None


def gid_of_row(row: str) -> str | None:
    """The G/R id a Next-pickup row names, or None for an id-less row."""
    match = _GID_RE.search(row or "")
    return match.group(1) if match else None


def gated_pickup_from_parsed(parsed: ParsedCheckpoint | None) -> LivePickup | None:
    """First gated ``Next-pickup`` row carrying a G/R id, or None.

    A gated row may be a bare closeout synonym (``CLOSEOUT``, ``arc-close``) with
    no id; those are skipped rather than treated as an advance target, so scanning
    continues to the next row and an id-less tip yields None (no advance, no
    admit).
    """
    if parsed is None:
        return None
    for row in parsed.next_pickup:
        if not item_is_gated(row):
            continue
        gid = gid_of_row(row)
        if gid is None:
            continue
        lane_match = _EXECUTOR_LANE_RE.search(row)
        executor_match = _EXECUTOR_RE.search(row)
        return LivePickup(
            gid=gid,
            row=row,
            lane=lane_match.group(1).lower() if lane_match else None,
            executor=executor_match.group(1) if executor_match else None,
        )
    return None


def worker_substrate_compatible(executor: str | None) -> bool:
    """Whether tip ``executor=`` may ride the worker generate seat.

    None / empty / ``pending`` leave the attended→generate path open (no tip
    authority). ``cursor/*`` is worker-native. ``cdp/*`` and any other family
    must refuse ``ADMIT_WORKER`` (a:26659 executor-mismatch class).
    """
    if executor is None:
        return True
    cleaned = str(executor).strip()
    if not cleaned or cleaned.lower() == "pending":
        return True
    return cleaned.startswith("cursor/")


def advance_pickup_gid(
    conn,
    row: RootLedgerRow,
    parsed: ParsedCheckpoint | None,
) -> LivePickup | None:
    """Move ``pickup_gid`` onto the tip's live gated row; return it when it moved.

    Idempotent: returns None (no write) when the ledger already names the live gid
    or when the tip has no gated row to advance to. Advancing starts a fresh
    consult cycle for the new gid — the durable consult queue is keyed
    ``(root, gid, role)``, so carrying the previous row's attempt count and
    backoff would charge a new pickup for the old one's retries.
    """
    live = gated_pickup_from_parsed(parsed)
    if live is None or live.gid == row.pickup_gid:
        return None
    advanced = replace(
        row,
        pickup_gid=live.gid,
        consult_role=None,
        consult_attempts=0,
        consult_next_retry=None,
        last_transition=Transition.ADVANCE_PICKUP.value,
        updated_at=time.time(),
    )
    upsert_root(conn, advanced)
    write_cortex_mirror(advanced)
    logger.info(
        "charter-runner pickup advance root=%s %s -> %s lane=%s row=%r",
        row.root_id,
        row.pickup_gid,
        live.gid,
        live.lane,
        live.row[:120],
    )
    return live


__all__ = [
    "LivePickup",
    "advance_pickup_gid",
    "gated_pickup_from_parsed",
    "gid_of_row",
    "worker_substrate_compatible",
]
