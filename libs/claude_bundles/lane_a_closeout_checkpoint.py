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
# Optional @plane infix (a:28271 / closeout-plane-legibility) — additive grammar.
_PLANE_INFIX = r"(?:@[\w.-]+(?:\([^)]*\))?)?"
_CHECKPOINT_COMMITTED_RE = re.compile(
    rf"^committed{_PLANE_INFIX}\s+([0-9a-f]{{7,40}})\s+paths=(\d+)"
    r"(?:\s+\(\+(\d+)\s+pending\))?\s*$",
    re.I,
)
_CHECKPOINT_NOTHING_RE = re.compile(
    rf"^nothing_authored{_PLANE_INFIX}\s*$",
    re.I,
)
_CHECKPOINT_DEFERRED_RE = re.compile(
    rf"^deferred{_PLANE_INFIX}:\s*(.+)$",
    re.I | re.S,
)
_CHECKPOINT_AUTHORED_CORTEX_RE = re.compile(
    rf"^authored_cortex{_PLANE_INFIX}:\s*(.+)$",
    re.I,
)
_AUTHORED_CORTEX_PAIR_RE = re.compile(
    r"^(cortex://\S+)\s+([0-9a-f]{64})$"
)

LANE_A_CHECKPOINT_FIX_HINT = (
    "Add a `checkpoint:` line to the CLOSEOUT body (fail-closed). Legal values: "
    "`checkpoint: committed <sha> paths=N` (path-explicit lane commit; optional "
    "`(+M pending)` when authored paths remain dirty; optional `@plane` infix "
    "e.g. `committed@local-master`), "
    "`checkpoint: authored_cortex: <cortex-uri> <sha256>` "
    "(semicolon-separated pairs for multi-write; optional `@plane`), "
    "`checkpoint: nothing_authored`, or `checkpoint: deferred: <reason>` "
    "(optional `@plane` on each). "
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


def normalize_checkpoint_value(value: str) -> str:
    """Strip markdown/backtick wrapping and doubled ``checkpoint:`` prefixes."""
    text = value.strip().strip("`").strip()
    lowered = text.casefold()
    while lowered.startswith("checkpoint:"):
        text = text.split(":", 1)[1].strip().strip("`").strip()
        lowered = text.casefold()
    return text


def _extract_checkpoint_value(body: str) -> str | None:
    match = _CHECKPOINT_LINE_RE.search(body or "")
    if match is None:
        return None
    return normalize_checkpoint_value(match.group(1))


def _authored_cortex_pairs_legal(rest: str) -> bool:
    """True when *rest* is one or more ``cortex://… <64-hex>`` pairs (``; `` sep)."""
    parts = [part.strip() for part in rest.split(";")]
    if not parts or any(not part for part in parts):
        return False
    for part in parts:
        match = _AUTHORED_CORTEX_PAIR_RE.match(part)
        if match is None:
            return False
        if not match.group(1).casefold().startswith("cortex://"):
            return False
    return True


def _checkpoint_value_legal(value: str) -> bool:
    if _CHECKPOINT_COMMITTED_RE.match(value):
        return True
    if _CHECKPOINT_NOTHING_RE.match(value):
        return True
    if _CHECKPOINT_DEFERRED_RE.match(value):
        return True
    authored = _CHECKPOINT_AUTHORED_CORTEX_RE.match(value)
    if authored is not None and _authored_cortex_pairs_legal(authored.group(1)):
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
    "normalize_checkpoint_value",
    "refusal_envelope",
    "validate_lane_a_closeout_checkpoint",
]
