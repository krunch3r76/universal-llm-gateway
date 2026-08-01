"""Fail-closed ``checkpoint:`` disposition gate for lane-A CLOSEOUT turns.

Lane A (cursor-auto ↔ operator-proxy) requires every CLOSEOUT to disclose what
the episode did with its authored paths: commit, nothing authored, or deferred.
``deferred: <reason>`` is always legal — disclosure, not obligation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CLOSEOUT_TYPE_RE = re.compile(r"(?i)^TYPE:\s*CLOSEOUT\b", re.M)
_CHECKPOINT_LINE_RE = re.compile(r"(?im)^checkpoint:\s*(.+)$")
_CHECKPOINT_COMMITTED_RE = re.compile(
    r"^committed\s+([0-9a-f]{7,40})\s+paths=(\d+)\s*$",
    re.I,
)
_CHECKPOINT_NOTHING_RE = re.compile(r"^nothing_authored\s*$", re.I)
_CHECKPOINT_DEFERRED_RE = re.compile(r"^deferred:\s*(.+)$", re.I | re.S)

LANE_A_CHECKPOINT_FIX_HINT = (
    "Add a `checkpoint:` line to the CLOSEOUT body (fail-closed). Legal values: "
    "`checkpoint: committed <sha> paths=N` (path-explicit lane commit), "
    "`checkpoint: nothing_authored`, or `checkpoint: deferred: <reason>`. "
    "Commit clears lane authorship — never `--all`, never foreign WIP. "
    "Commit is not a live/done gate; `deferred:` is always acceptable."
)


@dataclass(frozen=True, slots=True)
class LaneACheckpointVerdict:
    """Result of validating a lane-A CLOSEOUT checkpoint disposition."""

    ok: bool
    reason: str | None = None
    missed_tokens: tuple[str, ...] = ()
    fix_hint: str = LANE_A_CHECKPOINT_FIX_HINT
    checkpoint_value: str | None = None


def is_lane_a_closeout(*, subject: str = "", body: str = "") -> bool:
    """True when the turn body declares ``TYPE: CLOSEOUT``."""
    return bool(_CLOSEOUT_TYPE_RE.search(body or ""))


def _extract_checkpoint_value(body: str) -> str | None:
    match = _CHECKPOINT_LINE_RE.search(body or "")
    if match is None:
        return None
    return match.group(1).strip()


def _checkpoint_value_legal(value: str) -> bool:
    if _CHECKPOINT_COMMITTED_RE.match(value):
        return True
    if _CHECKPOINT_NOTHING_RE.match(value):
        return True
    if _CHECKPOINT_DEFERRED_RE.match(value):
        return True
    return False


def validate_lane_a_closeout_checkpoint(
    *,
    subject: str = "",
    body: str = "",
    require_closeout_type: bool = True,
) -> LaneACheckpointVerdict:
    """Refuse lane-A CLOSEOUT bodies that omit or malform ``checkpoint:``.

    When ``require_closeout_type`` is False (executor relay body before the
    cursor-auto envelope), only the ``checkpoint:`` line is required.
    """
    text = body or ""
    if require_closeout_type and not is_lane_a_closeout(subject=subject, body=text):
        return LaneACheckpointVerdict(ok=True)
    value = _extract_checkpoint_value(text)
    if value is None:
        return LaneACheckpointVerdict(
            ok=False,
            reason="lane_a_checkpoint_missing",
            missed_tokens=("checkpoint:",),
        )
    if not _checkpoint_value_legal(value):
        return LaneACheckpointVerdict(
            ok=False,
            reason="lane_a_checkpoint_malformed",
            missed_tokens=(f"checkpoint: {value[:120]}",),
            checkpoint_value=value,
        )
    return LaneACheckpointVerdict(ok=True, checkpoint_value=value)


def refusal_envelope(verdict: LaneACheckpointVerdict) -> dict[str, object]:
    """Structured MCP/cursor-auto refusal payload."""
    return {
        "error": (
            "Lane-A CLOSEOUT refused — missing or invalid checkpoint disposition "
            f"({verdict.reason})."
        ),
        "reason": verdict.reason or "lane_a_checkpoint_missing",
        "missed_tokens": list(verdict.missed_tokens),
        "fix_hint": verdict.fix_hint,
        "status": "blocked",
    }


__all__ = [
    "LANE_A_CHECKPOINT_FIX_HINT",
    "LaneACheckpointVerdict",
    "is_lane_a_closeout",
    "refusal_envelope",
    "validate_lane_a_closeout_checkpoint",
]
