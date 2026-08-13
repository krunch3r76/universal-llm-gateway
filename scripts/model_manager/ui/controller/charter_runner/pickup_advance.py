"""Advance the durable ledger pickup — typed record primary, tip optional journal.

``pickup_gid`` on the typed ledger row is admit/advance SoT (R2). The
CHECKPOINT tip may still advance pickup when present, but malformed or absent
tips do not block dispatch when the typed record is valid.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace

from universal_logging import get_logger

from .checkpoint_schema import ParsedCheckpoint, first_actionable_step, item_is_gated
from .root_ledger import (
    RootLedgerRow,
    Transition,
    record_advance_at,
    typed_record_valid,
    upsert_root,
    write_cortex_mirror,
)

logger = get_logger(__name__)

# Scoreboard G-rows and charter R-beats — same shape checkpoint_parse gates on.
_GID_RE = re.compile(r"\b([GR]\d+[a-z]?)\b")
# Optional markdown backticks around the value — tips routinely write
# ``executor=`cursor/grok-4.6` ``; without allowing `` ` `` the token regex
# captured empty and tip_is_empty_hopper permanently fenced admit (6237 after
# harvest heal).
_EXECUTOR_RE = re.compile(r"executor\s*=\s*`?([A-Za-z0-9._/\-]+)`?")
# Same charset as ``_EXECUTOR_RE``, but ``*`` so bare ``executor=`` matches.
# Do **not** use ``(.*)`` — tips routinely continue with ``· executor_lane: …``
# after the value; a greedy capture made ``pending`` compare fail (a:26710 resume).
_EXECUTOR_TOKEN_RE = re.compile(
    r"executor\s*=\s*`?([A-Za-z0-9._/\-]*)`?", re.IGNORECASE
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
    lowered = cleaned.lower()
    if lowered in {"cursor-sdk", "cursor"}:
        return True
    return lowered.startswith("cursor/")


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


def actionable_pickup_aligned(parsed: ParsedCheckpoint | None) -> bool:
    """True when gated Next-pickup matches the first incomplete Steps row gid.

    ``executor=pending`` on such a tip is bind-at-admit work (6237 G5b fold), not
    a standing-wait empty hopper.
    """
    if parsed is None:
        return False
    live = gated_pickup_from_parsed(parsed)
    actionable = first_actionable_step(parsed)
    if live is None or actionable is None:
        return False
    step_gid = gid_of_row(actionable.title)
    return step_gid is not None and step_gid == live.gid


def empty_hopper_row_rejections(
    parsed: ParsedCheckpoint | None,
) -> list[dict[str, str]]:
    """Per-row rejections when ``tip_is_empty_hopper`` would fire (AC4 telemetry)."""
    if parsed is None:
        return []
    live = gated_pickup_from_parsed(parsed)
    if live is None:
        return []
    if parsed.consult_pending or actionable_pickup_aligned(parsed):
        return []
    if tip_executor_is_explicitly_unbound(live):
        return [
            {
                "row_id": live.gid,
                "predicate": "tip_executor_is_explicitly_unbound",
                "reason": "executor_pending_or_empty",
            }
        ]
    return []


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

    ``CONSULT_PENDING`` tips are **not** empty hoppers — ``executor=pending``
    there means the consult seat owns the model (R-admit / judgment_gap). Treating
    them as empty_hopper fenced ``QUEUE_CONSULT`` forever (live 6237 G3 after w2).

    Actionable Steps alignment (first incomplete step gid == gated pickup gid) is
    also **not** an empty hopper — pending executor binds at admit (6237 G5b).
    """
    if has_wip or wip_window_id:
        return False
    if parsed is not None and parsed.consult_pending:
        return False
    if actionable_pickup_aligned(parsed):
        return False
    live = gated_pickup_from_parsed(parsed)
    if live is None:
        return False
    return tip_executor_is_explicitly_unbound(live)


def tip_executor_is_cdp_family(executor: str | None) -> bool:
    """True when tip ``executor=`` is a CDP / web-anthropic substrate family.

    Stage-B (a:26659): consult-shaped ``cdp/*`` tips rebind to ``QUEUE_CONSULT``.
    Worker-shaped tips refuse ``executor_mismatch`` instead (6489).
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
    """Align ledger pickup with the live gated tip; return tip when anything wrote.

    Writes when **any** of:
    - ``pickup_gid`` moves to the tip gid (fresh consult cycle — queue is keyed
      ``(root, gid, role)``, so attempt/backoff reset on gid change only)
    - tip ``executor_lane`` / ``executor=`` differ from ledger (same-gid densify →
      implement must sync lane or Path B reuses the densify work_key — 6563 G4)

    Idempotent: returns None when tip is absent or already fully aligned.
    """
    live = gated_pickup_from_parsed(parsed)
    if live is None:
        return None
    gid_changed = live.gid != row.pickup_gid
    ledger_lane = (row.pickup_lane or "").strip().lower() or None
    tip_lane = live.lane
    lane_changed = tip_lane is not None and tip_lane != ledger_lane
    tip_executor = live.executor
    ledger_executor = (row.pickup_executor or "").strip() or None
    tip_executor_norm = (tip_executor or "").strip() or None
    executor_changed = (
        tip_executor_norm is not None and tip_executor_norm != ledger_executor
    )
    if not gid_changed and not lane_changed and not executor_changed:
        return None
    advanced = replace(
        row,
        pickup_gid=live.gid,
        pickup_lane=tip_lane if tip_lane is not None else row.pickup_lane,
        pickup_executor=(
            tip_executor_norm if tip_executor_norm is not None else row.pickup_executor
        ),
        consult_role=None if gid_changed else row.consult_role,
        consult_attempts=0 if gid_changed else row.consult_attempts,
        consult_next_retry=None if gid_changed else row.consult_next_retry,
        last_transition=Transition.ADVANCE_PICKUP.value,
        updated_at=time.time(),
    )
    upsert_root(conn, advanced)
    write_cortex_mirror(advanced)
    if gid_changed:
        record_advance_at(conn, row.root_id)
    logger.info(
        "charter-runner pickup advance root=%s %s -> %s lane=%s->%s executor=%s->%s row=%r",
        row.root_id,
        row.pickup_gid,
        live.gid,
        ledger_lane,
        advanced.pickup_lane,
        ledger_executor,
        advanced.pickup_executor,
        live.row[:120],
    )
    return live


def typed_pickup_authority(row: RootLedgerRow) -> LivePickup | None:
    """Ledger pickup fields when typed admit is authoritative (tip optional)."""
    if not typed_record_valid(row):
        return None
    gid = str(row.pickup_gid or "").strip()
    if not gid:
        return None
    return LivePickup(
        gid=gid,
        row=f"{gid} — typed",
        lane=str(row.pickup_lane or "").lower() or None,
        executor=row.pickup_executor,
    )


__all__ = [
    "LivePickup",
    "actionable_pickup_aligned",
    "advance_pickup_gid",
    "empty_hopper_row_rejections",
    "gated_pickup_from_parsed",
    "gid_of_row",
    "tip_executor_is_cdp_family",
    "tip_executor_is_explicitly_unbound",
    "tip_is_empty_hopper",
    "typed_pickup_authority",
    "worker_substrate_compatible",
]
