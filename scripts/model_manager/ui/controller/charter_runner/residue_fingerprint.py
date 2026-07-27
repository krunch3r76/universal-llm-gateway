"""Residue fingerprint + progress witnesses for charter admission thrash gate.

Eligibility calls ``evaluate_residue_gate`` before admitting a window; harvest
persists the last residue so unchanged CHECKPOINT bodies cannot thrash forever.
Skip strikes advance only when ``window_index`` advances, not on every tick.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from .checkpoint_parse import ParsedCheckpoint, Step, pickup_detent
from .executor_routing import gated_row_classes
from .r_corpus_sha import extract_r_corpus_pin

UNCHANGED_RESIDUE_SKIP_THRESHOLD = 4
REASON_UNCHANGED_RESIDUE = "unchanged_residue"
REASON_NO_PROGRESS = "no_progress:unchanged_residue"

_SELF_HEAL_AUTHOR_RE = re.compile(
    r"charter-runner\s*\(\s*machine\s+self-heal",
    re.IGNORECASE,
)
_POLL_HINT_RE = re.compile(
    r"(?:poll_hint|from=cdp)\s*[=:]\s*(\S+)",
    re.IGNORECASE,
)
_EXECUTION_ID_RE = re.compile(r"execution_id\s*=\s*(\S+)", re.IGNORECASE)
_GENERATION_RE = re.compile(
    r"(?:heal:consult_stall\s+gen=\d+|Generation:\s*heal:consult_stall\s+gen=\d+)",
    re.IGNORECASE,
)
_CONSULT_PROVENANCE_RE = re.compile(
    r"^##\s+Consult provenance\b",
    re.IGNORECASE | re.MULTILINE,
)
_PROVENANCE_VERDICT_RE = re.compile(
    r"^\s*[-*]\s*verdict:\s*\S+",
    re.IGNORECASE | re.MULTILINE,
)
# Strip transport resume anchors from pickup rows before fingerprinting.
_PICKUP_STRIP_RE = re.compile(
    r"\s*[·•]\s*(?:poll_hint|from=cdp|execution_id)\s*[=:]\s*\S+|\s*"
    r"(?:poll_hint|from=cdp|execution_id)\s*[=:]\s*\S+|\s*"
    r"heal:consult_stall\s+gen=\d+",
    re.IGNORECASE,
)
WindowKind = Literal["worker", "consult"]


@dataclass(frozen=True)
class WitnessTuple:
    normalized_next_pickup: tuple[str, ...]
    steps_done_count: int
    poll_hint: str | None
    execution_id: str | None
    generation_marker: str | None
    consult_provenance_present: bool
    spec_sha256: str | None
    steps_signature: tuple[tuple[int, str, str], ...]
    self_heal_author: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "normalized_next_pickup": list(self.normalized_next_pickup),
            "steps_done_count": self.steps_done_count,
            "poll_hint": self.poll_hint,
            "execution_id": self.execution_id,
            "generation_marker": self.generation_marker,
            "consult_provenance_present": self.consult_provenance_present,
            "spec_sha256": self.spec_sha256,
            "steps_signature": [list(item) for item in self.steps_signature],
            "self_heal_author": self.self_heal_author,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WitnessTuple:
        sig_raw = raw.get("steps_signature") or []
        signature = tuple(
            (int(item[0]), str(item[1]), str(item[2]))
            for item in sig_raw
            if isinstance(item, (list, tuple)) and len(item) == 3
        )
        pickup_raw = raw.get("normalized_next_pickup") or []
        return cls(
            normalized_next_pickup=tuple(str(x) for x in pickup_raw),
            steps_done_count=int(raw.get("steps_done_count") or 0),
            poll_hint=raw.get("poll_hint"),
            execution_id=raw.get("execution_id"),
            generation_marker=raw.get("generation_marker"),
            consult_provenance_present=bool(raw.get("consult_provenance_present")),
            spec_sha256=raw.get("spec_sha256"),
            steps_signature=signature,
            self_heal_author=bool(raw.get("self_heal_author")),
        )


@dataclass(frozen=True)
class ResidueRecord:
    fingerprint: str
    witness: WitnessTuple
    consecutive_skip_count: int = 0
    w10_consumed: bool = False
    last_window_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "witness": self.witness.to_dict(),
            "consecutive_skip_count": self.consecutive_skip_count,
            "w10_consumed": self.w10_consumed,
            "last_window_index": self.last_window_index,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ResidueRecord | None:
        fp = raw.get("fingerprint")
        witness_raw = raw.get("witness")
        if not isinstance(fp, str) or not fp or not isinstance(witness_raw, dict):
            return None
        return cls(
            fingerprint=fp,
            witness=WitnessTuple.from_dict(witness_raw),
            consecutive_skip_count=int(raw.get("consecutive_skip_count") or 0),
            w10_consumed=bool(raw.get("w10_consumed")),
            last_window_index=int(raw.get("last_window_index") or 0),
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


def normalize_pickup_row(text: str) -> str:
    """Strip transport tokens so residue compares semantic pickup, not poll ids."""
    row = _PICKUP_STRIP_RE.sub("", text.strip())
    row = re.sub(
        r"\(\s*charter-runner\s+window\s+\d+\s*\)",
        "",
        row,
        flags=re.IGNORECASE,
    )
    row = re.sub(r"\bwindow\s+\d+\b", "window", row, flags=re.IGNORECASE)
    return " ".join(row.split())


def normalize_next_pickup(parsed: ParsedCheckpoint) -> tuple[str, ...]:
    """Return transport-stripped Next-pickup rows used in residue INCLUDE hashing."""
    return tuple(
        normalize_pickup_row(item) for item in parsed.next_pickup if item.strip()
    )


def steps_signature(steps: list[Step]) -> tuple[tuple[int, str, str], ...]:
    """Stable (ordinal, title, status) tuple used as residue INCLUDE field W8."""
    return tuple((s.ordinal, s.title, s.status) for s in steps)


def steps_done_count(steps: list[Step]) -> int:
    """Count steps marked done — witness W2 fires when this increases."""
    return sum(1 for s in steps if s.status == "done")


def _extract_poll_hint(body: str, pickup: list[str]) -> str | None:
    for text in [*pickup, body]:
        match = _POLL_HINT_RE.search(text)
        if match:
            return match.group(1).rstrip(".,)")
    return None


def _extract_execution_id(body: str, pickup: list[str]) -> str | None:
    for text in [*pickup, body]:
        match = _EXECUTION_ID_RE.search(text)
        if match:
            return match.group(1).rstrip(".,)")
    return None


def _extract_generation_marker(body: str) -> str | None:
    match = _GENERATION_RE.search(body)
    return match.group(0) if match else None


def _consult_provenance_present(body: str) -> bool:
    if not _CONSULT_PROVENANCE_RE.search(body):
        return False
    return _PROVENANCE_VERDICT_RE.search(body) is not None


def _self_heal_author(body: str) -> bool:
    anchor_match = re.search(
        r"^##\s+Anchor\b.*?(?=^##\s|\Z)",
        body,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    anchor = anchor_match.group(0) if anchor_match else body[:400]
    return bool(_SELF_HEAL_AUTHOR_RE.search(anchor))


def _gated_class_set(parsed: ParsedCheckpoint) -> frozenset[str]:
    classes: set[str] = set()
    for row in parsed.next_pickup:
        classes |= gated_row_classes(row)
    return frozenset(classes)


def build_witness_tuple(
    *,
    checkpoint_body: str,
    parsed: ParsedCheckpoint,
) -> WitnessTuple:
    """Extract progress witnesses (pickup, steps, poll/exec ids, pin) from a body."""
    body = checkpoint_body or ""
    pickup = list(parsed.next_pickup)
    pin = extract_r_corpus_pin(body)
    spec_sha = pin.pinned_hex if pin.ok else None
    return WitnessTuple(
        normalized_next_pickup=normalize_next_pickup(parsed),
        steps_done_count=steps_done_count(parsed.steps),
        poll_hint=_extract_poll_hint(body, pickup),
        execution_id=_extract_execution_id(body, pickup),
        generation_marker=_extract_generation_marker(body),
        consult_provenance_present=_consult_provenance_present(body),
        spec_sha256=spec_sha,
        steps_signature=steps_signature(parsed.steps),
        self_heal_author=_self_heal_author(body),
    )


def compute_fingerprint(
    *,
    parsed: ParsedCheckpoint,
    admission_mode: str,
    window_kind: WindowKind,
    witness: WitnessTuple,
) -> str:
    """Hash residue INCLUDE fields only — transport ids are deliberately omitted."""
    payload = {
        "normalized_next_pickup": list(witness.normalized_next_pickup),
        "pickup_detent": pickup_detent(parsed),
        "admission_mode": admission_mode,
        "window_kind": window_kind,
        "consult_pending": parsed.consult_pending,
        "consult_role": parsed.consult_role,
        "executor_lane": parsed.executor_lane,
        "executor_lane_ambiguous": parsed.executor_lane_ambiguous,
        "source_ref": parsed.source_ref,
        "blocked": parsed.blocked,
        "steps_signature": [list(item) for item in witness.steps_signature],
        "scoreboard_uri": parsed.scoreboard_uri,
        "spec_sha256": witness.spec_sha256,
        "gated_row_classes": sorted(_gated_class_set(parsed)),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def witness_fired(current: WitnessTuple, last: WitnessTuple) -> tuple[bool, str | None]:
    """Return whether a progress witness fired and which id (W1–W8; W9 open)."""
    if current.normalized_next_pickup != last.normalized_next_pickup:
        return True, "W1"
    if current.steps_done_count > last.steps_done_count:
        return True, "W2"
    if current.poll_hint != last.poll_hint:
        return True, "W3"
    if current.execution_id != last.execution_id:
        return True, "W4"
    if current.generation_marker != last.generation_marker:
        return True, "W5"
    if current.consult_provenance_present and not last.consult_provenance_present:
        return True, "W6"
    if current.spec_sha256 != last.spec_sha256:
        return True, "W7"
    if current.steps_signature != last.steps_signature:
        return True, "W8"
    # W9 scoreboard_lane_hash — open fork; hook only (not implemented).
    return False, None


def w10_allows_admit(
    *,
    fingerprint_matches: bool,
    current: WitnessTuple,
    last: ResidueRecord,
) -> bool:
    """One-shot admit for machine self-heal authors when residue is unchanged."""
    return fingerprint_matches and current.self_heal_author and not last.w10_consumed


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
    "evaluate_residue_gate",
    "load_residue_record",
    "normalize_next_pickup",
    "record_from_harvest",
    "save_residue_record",
    "witness_fired",
    "w10_allows_admit",
]
