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
# Same charset as ``_EXECUTOR_RE``, but ``*`` so bare ``executor=`` matches.
# Do **not** use ``(.*)`` — tips routinely continue with ``· executor_lane: …``
# after the value; a greedy capture made ``pending`` compare fail (a:26710 resume).
_EXECUTOR_TOKEN_RE = re.compile(
    r"executor\s*=\s*([A-Za-z0-9._/\-]*)", re.IGNORECASE
)
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


def tip_executor_is_explicitly_unbound(live: LivePickup | None) -> bool:
    """Whether the gated tip row names ``executor=`` with empty or pending value.

    Inputs: ``live`` — gated pickup extracted from the tip (or None).
    Output: True when the row contains an ``executor=`` token **and** the value
    is blank or ``pending`` (case-insensitive). A gated row that **omits**
    ``executor=`` entirely is **not** unbound (fail-open to existing admit
    behaviour — distinct from ``worker_substrate_compatible(None)``).
    """
    if live is None:
        return False
    match = _EXECUTOR_TOKEN_RE.search(live.row)
    if match is None:
        return False
    value = match.group(1).strip()
    if not value:
        return True
    return value.lower() == "pending"


def tip_is_empty_hopper(
    parsed: ParsedCheckpoint | None,
    *,
    has_wip: bool,
    wip_window_id: str | None,
) -> bool:
    """True when a gated tip is an empty hopper (standing wait, no actionable work).

    Predicate: gated Next-pickup present, no in-flight WIP (bus or ledger
    ``wip_window_id``), and tip ``executor=`` is explicitly empty or ``pending``.
    Missing ``executor=`` token ⇒ False (fail-open).
    """
    if has_wip or wip_window_id:
        return False
    live = gated_pickup_from_parsed(parsed)
    if live is None:
        return False
    return tip_executor_is_explicitly_unbound(live)


def tip_executor_is_cdp_family(executor: str | None) -> bool:
    """True when tip ``executor=`` is a CDP / web-anthropic substrate family.

    Stage-B (a:26659 elaboration): incompatible ``cdp/*`` tips positively rebind
    to ``QUEUE_CONSULT`` instead of bare refuse — never ``ADMIT_WORKER``.
    """
    if executor is None:
        return False
    cleaned = str(executor).strip().lower()
    return cleaned.startswith("cdp/")


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
    "tip_executor_is_cdp_family",
    "tip_executor_is_explicitly_unbound",
    "tip_is_empty_hopper",
    "worker_substrate_compatible",
]
