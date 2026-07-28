"""Residue thrash gate — evaluate fingerprint/witness against last record.

Witness construction lives in ``residue_witness``; store I/O in ``residue_store``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .checkpoint_parse import ParsedCheckpoint
from .residue_witness import (
    REASON_NO_PROGRESS,
    REASON_UNCHANGED_RESIDUE,
    UNCHANGED_RESIDUE_SKIP_THRESHOLD,
    ResidueRecord,
    WindowKind,
    WitnessTuple,
    build_witness_tuple,
    compute_fingerprint,
    consult_provenance_present,
    normalize_next_pickup,
    w10_allows_admit,
    witness_fired,
)


@dataclass(frozen=True)
class ResidueGateVerdict:
    admit: bool
    reason: str
    fingerprint: str
    witness: WitnessTuple
    consecutive_skip_count: int
    w10_consumed: bool
    stop_root: bool = False
    last_window_index: int = 0


# W9 scoreboard_lane_hash — open fork; hook only (implemented in residue_witness.witness_fired).


def evaluate_residue_gate(
    *,
    checkpoint_body: str,
    parsed: ParsedCheckpoint,
    admission_mode: str,
    window_kind: WindowKind,
    last: ResidueRecord | None,
    window_index: int,
) -> ResidueGateVerdict:
    """Admit or skip based on residue fingerprint; strikes count per window advance."""
    witness = build_witness_tuple(checkpoint_body=checkpoint_body, parsed=parsed)
    fingerprint = compute_fingerprint(
        parsed=parsed,
        admission_mode=admission_mode,
        window_kind=window_kind,
        witness=witness,
    )
    if last is None:
        return ResidueGateVerdict(
            admit=True,
            reason="eligible",
            fingerprint=fingerprint,
            witness=witness,
            consecutive_skip_count=0,
            w10_consumed=False,
            last_window_index=window_index,
        )
    fingerprint_matches = fingerprint == last.fingerprint
    if not fingerprint_matches:
        return ResidueGateVerdict(
            admit=True,
            reason="eligible",
            fingerprint=fingerprint,
            witness=witness,
            consecutive_skip_count=0,
            w10_consumed=False,
            last_window_index=window_index,
        )
    fired, _ = witness_fired(witness, last.witness)
    if fired:
        return ResidueGateVerdict(
            admit=True,
            reason="eligible",
            fingerprint=fingerprint,
            witness=witness,
            consecutive_skip_count=0,
            w10_consumed=False,
            last_window_index=window_index,
        )
    if w10_allows_admit(fingerprint_matches=True, current=witness, last=last):
        return ResidueGateVerdict(
            admit=True,
            reason="eligible",
            fingerprint=fingerprint,
            witness=witness,
            consecutive_skip_count=0,
            w10_consumed=True,
            last_window_index=window_index,
        )
    # Count strikes per admitted-window advance, not per tick (latency a:5918).
    window_advanced = window_index != last.last_window_index
    new_skip = last.consecutive_skip_count + (1 if window_advanced else 0)
    if new_skip >= UNCHANGED_RESIDUE_SKIP_THRESHOLD:
        return ResidueGateVerdict(
            admit=False,
            reason=REASON_NO_PROGRESS,
            fingerprint=fingerprint,
            witness=witness,
            consecutive_skip_count=new_skip,
            w10_consumed=last.w10_consumed,
            stop_root=True,
            last_window_index=window_index,
        )
    return ResidueGateVerdict(
        admit=False,
        reason=REASON_UNCHANGED_RESIDUE,
        fingerprint=fingerprint,
        witness=witness,
        consecutive_skip_count=new_skip,
        w10_consumed=last.w10_consumed,
        last_window_index=window_index,
    )


# Store I/O lives in residue_store; re-export keeps call-site imports stable.
from .residue_store import (  # noqa: E402
    load_residue_record,
    save_residue_record,
)


def record_from_harvest(
    *,
    checkpoint_body: str,
    parsed: ParsedCheckpoint,
    admission_mode: str,
    window_kind: WindowKind,
    w10_consumed: bool = False,
) -> ResidueRecord:
    """Build a fresh residue record from the CHECKPOINT a harvested window consumed."""
    witness = build_witness_tuple(checkpoint_body=checkpoint_body, parsed=parsed)
    fingerprint = compute_fingerprint(
        parsed=parsed,
        admission_mode=admission_mode,
        window_kind=window_kind,
        witness=witness,
    )
    return ResidueRecord(
        fingerprint=fingerprint,
        witness=witness,
        consecutive_skip_count=0,
        w10_consumed=w10_consumed,
    )


__all__ = [
    "REASON_NO_PROGRESS",
    "REASON_UNCHANGED_RESIDUE",
    "ResidueGateVerdict",
    "ResidueRecord",
    "UNCHANGED_RESIDUE_SKIP_THRESHOLD",
    "WitnessTuple",
    "build_witness_tuple",
    "compute_fingerprint",
    "consult_provenance_present",
    "evaluate_residue_gate",
    "load_residue_record",
    "normalize_next_pickup",
    "record_from_harvest",
    "save_residue_record",
    "witness_fired",
    "w10_allows_admit",
]
