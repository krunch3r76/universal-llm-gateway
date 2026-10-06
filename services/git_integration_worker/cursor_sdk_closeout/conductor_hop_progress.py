"""Hop progress signature — what counts as evidence that a mission advanced.

A no-progress park accuses a mission of looping. That accusation is sound
only when a signal that *can* move failed to move. The scoreboard fold alone
cannot carry it: when a scoreboard's Status column holds words the fold does
not accept as witnesses, the fold reports the first gate with an empty
witness set on every hop, so three hops that each shipped a commit read
identically to three hops that did nothing (``assertion:32411``).

This module records the several independent facts that move when a conductor
mission advances, and keeps two questions apart:

``signature_advanced``       — did anything move between two hops?
``signature_can_prove_loop``   — is any component able to show movement at all,
                                so that its stillness is evidence?
``signature_can_prove_crash``  — can a crash be attributed to a specific row,
                                so repeated crashes count toward the cap?

The scoreboard tip sha (``hop_scoreboard_tip``) is the one component the
conductor moves itself on every hop that records anything — a bind hung, a
stop noted, a gate advanced. It keeps the detector honest when the witness
fold is blind to the mission's rows: on 2026-10-01 (worker 13713) three hops
each hung a new R-row bind while the fold read the G-ladder and stayed at
G2/{G1}, and the lane tip was unreadable because the branch had been
archived at closeout, so the mission was parked for "no progress".
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
HOP_SCOREBOARD_TIP_KEY = "hop_scoreboard_tip"
# Returned when no fold is available. Indistinguishable from a genuine first
# gate, which is exactly why an empty witness set never justifies a park.
UNPAID_ENTRY_GATE = "G1"

_GIT_TIMEOUT_S = 15.0
_NEXT_ADMIT_RE = re.compile(
    r"(?im)^[^\S\n]*NEXT_ADMIT[^\S\n]*:[^\S\n]*(\S.*?)[^\S\n]*$"
)


@dataclass(frozen=True, slots=True)
class HopProgressSignature:
    """Independent facts that move when a conductor mission advances.

    ``scoreboard_tip`` defaults to ``None`` so signatures built before the
    component existed (older ledger stamps, call sites that name only the
    first four fields) keep their meaning: an absent tip is unknown, never a
    claim that the tip stood still.
    """

    entry_gate: str
    witnessed_done: frozenset[str]
    lane_tip: str | None
    next_admit: str | None
    scoreboard_tip: str | None = None


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


def _fold_for_slug(slug: str, row: dict[str, Any]) -> Any | None:
    from implement_admission.conductor_witness import fold_scoreboard

    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _fold_repo,
        _fold_summon_kwargs,
        _record_data,
    )
    from services.git_integration_worker.cursor_sdk_nested_witness import (
        fold_deps_with_ledger,
    )

    rec = _record_data(row)
    return fold_scoreboard(
        slug,
        deps=fold_deps_with_ledger(
            f"todo:{slug}",
            repo=_fold_repo(row),
            **_fold_summon_kwargs(rec),
        ),
        write_journal=False,
    )


def entry_gate_for_row(row: dict[str, Any], *, live: bool = True) -> str:
    """Stamped entry gate, else a live fold, else the unpaid sentinel.

    ``live=False`` is for historical hops in a budget chain: today's fold
    is not what that hop recorded.
    """
    record = record_data(str(row.get("record_json") or ""))
    gate = record.get(HOP_ENTRY_GATE_KEY)
    if isinstance(gate, str) and gate:
        return gate
    if not live:
        return UNPAID_ENTRY_GATE
    slug = _slug_for_row(row)
    if slug is None:
        return UNPAID_ENTRY_GATE
    try:
        from implement_admission.conductor_witness import resolve_entry_gate_from_fold

        fold = _fold_for_slug(slug, row)
        if fold is not None:
            return resolve_entry_gate_from_fold(fold)
    except Exception as exc:  # noqa: BLE001 — fold is advisory
        logger.warning("hop progress entry_gate fold failed slug=%s err=%s", slug, exc)
    return UNPAID_ENTRY_GATE


def witnessed_done_for_row(row: dict[str, Any], *, live: bool = True) -> frozenset[str]:
    """Stamped witness set, else a live fold, else empty (nothing witnessed)."""
    record = record_data(str(row.get("record_json") or ""))
    raw = record.get(HOP_WITNESSED_DONE_KEY)
    if isinstance(raw, list):
        return frozenset(str(v) for v in raw)
    if not live:
        return frozenset()
    slug = _slug_for_row(row)
    if slug is None:
        return frozenset()
    try:
        fold = _fold_for_slug(slug, row)
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


def lane_tip_for_row(row: dict[str, Any], *, live: bool = True) -> str | None:
    """Lane branch tip — moves whenever a hop ships a commit.

    Live ``git rev-parse`` is only for the hop under evaluation. Unstamped
    priors must stay ``None``: reading today's head onto hops 1–3 makes a
    shipping hop look identical to its history (hop 4 park, 2026-09-06).
    """
    record = record_data(str(row.get("record_json") or ""))
    stamped = record.get(HOP_LANE_TIP_KEY)
    if isinstance(stamped, str) and stamped:
        return stamped
    if not live:
        return None
    return read_lane_tip(
        source_repo=str(row.get("source_repo") or ""),
        thread_id=str(row.get("thread_id") or ""),
    )


def read_scoreboard_tip(*, slug: str) -> str | None:
    """Sha256 of the mission scoreboard tip on disk; None when unreadable.

    Goes through ``read_tip`` so a tip healed from its journal reads the same
    way the hop reactor and the witness fold read it. Kept as a module-level
    seam (like ``read_lane_tip``) so unit tests can replace the disk read:
    ``read_tip`` may rewrite a stale tip from the journal, which a test must
    never trigger against the live cortex root.
    """
    if not slug:
        return None
    try:
        from implement_admission.conductor_score_journal import read_tip

        tip = read_tip(slug)
    except Exception as exc:  # noqa: BLE001 — tip read is advisory
        logger.warning("hop progress scoreboard tip read failed slug=%s err=%s", slug, exc)
        return None
    if tip is None:
        return None
    _body, sha = tip
    return sha or None


def scoreboard_tip_for_row(row: dict[str, Any], *, live: bool = True) -> str | None:
    """Scoreboard tip sha as this hop left it — moves whenever the conductor records.

    Stamped ``hop_scoreboard_tip`` first. ``live=True`` reads today's tip for
    the hop now closing; ``live=False`` is for prior hops in a budget chain
    and never reads today's tip onto them (the same rule as
    ``lane_tip_for_row``). ``None`` when the mission has no todo slug or the
    tip is unreadable, which leaves the component unknown rather than still.
    """
    record = record_data(str(row.get("record_json") or ""))
    stamped = record.get(HOP_SCOREBOARD_TIP_KEY)
    if isinstance(stamped, str) and stamped:
        return stamped
    if not live:
        return None
    slug = _slug_for_row(row)
    if slug is None:
        return None
    return read_scoreboard_tip(slug=slug)


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


def progress_signature_for_row(
    row: dict[str, Any], *, live: bool = True
) -> HopProgressSignature:
    """Every progress component this terminal row can supply.

    ``live=True`` (default) stamps the hop now closing. ``live=False``
    reconstructs a prior hop from ledger stamps only.
    """
    return HopProgressSignature(
        entry_gate=entry_gate_for_row(row, live=live),
        witnessed_done=witnessed_done_for_row(row, live=live),
        lane_tip=lane_tip_for_row(row, live=live),
        next_admit=next_admit_for_row(row),
        scoreboard_tip=scoreboard_tip_for_row(row, live=live),
    )


def signature_advanced(
    newer: HopProgressSignature,
    older: HopProgressSignature,
) -> bool:
    """True when any component recorded on both hops moved between them.

    A scoreboard tip that changed between two hops counts as movement on its
    own: the conductor rewrote the tip, so the hop did something the fold may
    or may not be able to see. A tip missing on either side is unknown, not
    stillness.
    """
    if newer.entry_gate != older.entry_gate:
        return True
    if newer.witnessed_done - older.witnessed_done:
        return True
    if newer.lane_tip and older.lane_tip and newer.lane_tip != older.lane_tip:
        return True
    if newer.next_admit and older.next_admit and newer.next_admit != older.next_admit:
        return True
    if (
        newer.scoreboard_tip
        and older.scoreboard_tip
        and newer.scoreboard_tip != older.scoreboard_tip
    ):
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
    that both hops actually recorded — a lane tip, a named ``NEXT_ADMIT``, or
    a scoreboard tip sha. Two hops that both stamped a tip and left it
    identical did not record anything on the board, which is evidence.
    """
    if newer.witnessed_done or older.witnessed_done:
        return True
    if newer.lane_tip and older.lane_tip:
        return True
    if newer.next_admit and older.next_admit:
        return True
    if newer.scoreboard_tip and older.scoreboard_tip:
        return True
    return False


def signature_can_prove_crash(signature: HopProgressSignature) -> bool:
    """True when a crash can be charged to a specific scoreboard row.

    The unpaid-fold shape (``G1`` with nothing witnessed and no lane tip or
    ``NEXT_ADMIT``) cannot attribute crashes to a row — the instrument is
    unpaid, not the mission defective. A stamped entry gate alone is a table
    projection, not row identity (``assertion:32411``).
    """
    if signature.witnessed_done:
        return True
    if signature.lane_tip:
        return True
    if signature.next_admit:
        return True
    return False


def signatures_share_crash_row(
    newer: HopProgressSignature,
    older: HopProgressSignature,
) -> bool:
    """True when two hops crashed on the same provable scoreboard row."""
    if newer.entry_gate != older.entry_gate:
        return False
    if not signature_can_prove_crash(newer) or not signature_can_prove_crash(older):
        return False
    if newer.witnessed_done != older.witnessed_done:
        return False
    if newer.lane_tip != older.lane_tip:
        return False
    if newer.next_admit != older.next_admit:
        return False
    return True


__all__ = [
    "HOP_ENTRY_GATE_KEY",
    "HOP_LANE_TIP_KEY",
    "HOP_NEXT_ADMIT_KEY",
    "HOP_SCOREBOARD_TIP_KEY",
    "HOP_WITNESSED_DONE_KEY",
    "UNPAID_ENTRY_GATE",
    "HopProgressSignature",
    "entry_gate_for_row",
    "lane_tip_for_row",
    "next_admit_for_row",
    "next_admit_in_closeout",
    "progress_signature_for_row",
    "read_lane_tip",
    "read_scoreboard_tip",
    "record_data",
    "scoreboard_tip_for_row",
    "signature_advanced",
    "signature_can_prove_crash",
    "signature_can_prove_loop",
    "signatures_share_crash_row",
    "witnessed_done_for_row",
]
