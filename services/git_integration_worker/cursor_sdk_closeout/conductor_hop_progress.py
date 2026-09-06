"""Hop progress signature — what counts as evidence that a mission advanced.

A no-progress park accuses a mission of looping. That accusation is sound
only when a signal that *can* move failed to move. The scoreboard fold alone
cannot carry it: when a scoreboard's Status column holds words the fold does
not accept as witnesses, the fold reports the first gate with an empty
witness set on every hop, so three hops that each shipped a commit read
identically to three hops that did nothing (``assertion:32411``).

This module records the several independent facts that move when a conductor
mission advances, and keeps two questions apart:

``signature_advanced``      — did anything move between two hops?
``signature_can_prove_loop`` — is any component able to show movement at all,
                               so that its stillness is evidence?
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

HOP_ENTRY_GATE_KEY = "hop_entry_gate"
HOP_WITNESSED_DONE_KEY = "hop_witnessed_done"
HOP_LANE_TIP_KEY = "hop_lane_tip"
HOP_NEXT_ADMIT_KEY = "hop_next_admit"

# Returned when no fold is available. Indistinguishable from a genuine first
# gate, which is exactly why an empty witness set never justifies a park.
UNPAID_ENTRY_GATE = "G1"

_GIT_TIMEOUT_S = 15.0
_NEXT_ADMIT_RE = re.compile(
    r"(?im)^[^\S\n]*NEXT_ADMIT[^\S\n]*:[^\S\n]*(\S.*?)[^\S\n]*$"
)


@dataclass(frozen=True, slots=True)
class HopProgressSignature:
    """Independent facts that move when a conductor mission advances."""

    entry_gate: str
    witnessed_done: frozenset[str]
    lane_tip: str | None
    next_admit: str | None


def record_data(record_json: str | None) -> dict[str, Any]:
    """Parse a ledger ``record_json`` blob; malformed JSON reads as empty."""
    if not record_json:
        return {}
    try:
        data = json.loads(record_json)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _slug_for_row(row: dict[str, Any]) -> str | None:
    work_key = str(row.get("work_key") or "")
    if not work_key.startswith("todo:"):
        return None
    return work_key.split(":", 1)[1].strip() or None


def _fold_for_slug(slug: str) -> Any | None:
    from implement_admission.conductor_witness import fold_scoreboard
    from implement_admission.conductor_witness_defaults import DefaultWitnessCortex
    from implement_admission.conductor_witness_types import FoldDeps

    return fold_scoreboard(
        slug,
        deps=FoldDeps(cortex=DefaultWitnessCortex()),
        write_journal=False,
    )


def entry_gate_for_row(row: dict[str, Any]) -> str:
    """Stamped entry gate, else a live fold, else the unpaid sentinel."""
    record = record_data(str(row.get("record_json") or ""))
    gate = record.get(HOP_ENTRY_GATE_KEY)
    if isinstance(gate, str) and gate:
        return gate
    slug = _slug_for_row(row)
    if slug is None:
        return UNPAID_ENTRY_GATE
    try:
        from implement_admission.conductor_witness import resolve_entry_gate_from_fold

        fold = _fold_for_slug(slug)
        if fold is not None:
            return resolve_entry_gate_from_fold(fold)
    except Exception as exc:  # noqa: BLE001 — fold is advisory
        logger.warning("hop progress entry_gate fold failed slug=%s err=%s", slug, exc)
    return UNPAID_ENTRY_GATE


def witnessed_done_for_row(row: dict[str, Any]) -> frozenset[str]:
    """Stamped witness set, else a live fold, else empty (nothing witnessed)."""
    record = record_data(str(row.get("record_json") or ""))
    raw = record.get(HOP_WITNESSED_DONE_KEY)
    if isinstance(raw, list):
        return frozenset(str(v) for v in raw)
    slug = _slug_for_row(row)
    if slug is None:
        return frozenset()
    try:
        fold = _fold_for_slug(slug)
        if fold is not None:
            return fold.witnessed_done
    except Exception as exc:  # noqa: BLE001 — fold is advisory
        logger.warning(
            "hop progress witnessed_done fold failed slug=%s err=%s", slug, exc
        )
    return frozenset()


def read_lane_tip(*, source_repo: str, thread_id: str) -> str | None:
    """Head sha of ``cursor-sdk/lane-{thread}``; None when unresolvable."""
    if not source_repo or not thread_id:
        return None
    from services.git_integration_worker.cursor_sdk_worktree import lane_branch_name

    repo = Path(source_repo)
    if not repo.is_dir():
        return None
    ref = f"refs/heads/{lane_branch_name(thread_id)}"
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo.resolve()), "rev-parse", "--verify", ref],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("hop progress lane tip read failed ref=%s err=%s", ref, exc)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def lane_tip_for_row(row: dict[str, Any]) -> str | None:
    """Lane branch tip — moves whenever a hop ships a commit."""
    record = record_data(str(row.get("record_json") or ""))
    stamped = record.get(HOP_LANE_TIP_KEY)
    if isinstance(stamped, str) and stamped:
        return stamped
    return read_lane_tip(
        source_repo=str(row.get("source_repo") or ""),
        thread_id=str(row.get("thread_id") or ""),
    )


def next_admit_in_closeout(body: str) -> str | None:
    """The ``NEXT_ADMIT:`` target a conductor named for its successor."""
    match = _NEXT_ADMIT_RE.search(body or "")
    if match is None:
        return None
    return match.group(1).strip() or None


def next_admit_for_row(row: dict[str, Any]) -> str | None:
    """``NEXT_ADMIT`` stamped from this row's closeout, when it named one."""
    record = record_data(str(row.get("record_json") or ""))
    value = record.get(HOP_NEXT_ADMIT_KEY)
    return value if isinstance(value, str) and value else None


def progress_signature_for_row(row: dict[str, Any]) -> HopProgressSignature:
    """Every progress component this terminal row can supply."""
    return HopProgressSignature(
        entry_gate=entry_gate_for_row(row),
        witnessed_done=witnessed_done_for_row(row),
        lane_tip=lane_tip_for_row(row),
        next_admit=next_admit_for_row(row),
    )


def signature_advanced(
    newer: HopProgressSignature,
    older: HopProgressSignature,
) -> bool:
    """True when any component recorded on both hops moved between them."""
    if newer.entry_gate != older.entry_gate:
        return True
    if newer.witnessed_done - older.witnessed_done:
        return True
    if newer.lane_tip and older.lane_tip and newer.lane_tip != older.lane_tip:
        return True
    if newer.next_admit and older.next_admit and newer.next_admit != older.next_admit:
        return True
    return False


def signature_can_prove_loop(
    newer: HopProgressSignature,
    older: HopProgressSignature,
) -> bool:
    """True when some component could have moved, so its stillness is evidence.

    An empty ``witnessed_done`` on both hops means the fold has never accepted
    a witness for this mission: the instrument is unpaid, not the mission
    stalled. Such a fold proves nothing by itself, so a park needs a component
    that both hops actually recorded — a lane tip or a named ``NEXT_ADMIT``.
    """
    if newer.witnessed_done or older.witnessed_done:
        return True
    if newer.lane_tip and older.lane_tip:
        return True
    if newer.next_admit and older.next_admit:
        return True
    return False


__all__ = [
    "HOP_ENTRY_GATE_KEY",
    "HOP_LANE_TIP_KEY",
    "HOP_NEXT_ADMIT_KEY",
    "HOP_WITNESSED_DONE_KEY",
    "UNPAID_ENTRY_GATE",
    "HopProgressSignature",
    "entry_gate_for_row",
    "lane_tip_for_row",
    "next_admit_for_row",
    "next_admit_in_closeout",
    "progress_signature_for_row",
    "read_lane_tip",
    "record_data",
    "signature_advanced",
    "signature_can_prove_loop",
    "witnessed_done_for_row",
]
