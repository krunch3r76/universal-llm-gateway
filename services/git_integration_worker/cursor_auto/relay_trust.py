"""Trust policy for synthesized §2 CLOSEOUT relays (5968 ratified #3)."""

from __future__ import annotations

import json
import re
from typing import Any

_SYNTHESIZED_SOURCE = "section2_synthesized"
_ACK_RE = re.compile(r"(?im)^synthesized_closeout_ack\s*[:=]\s*(\S+)")
_META_LINE_RE = re.compile(r"(?im)^meta:\s*(.+)$")

# 5968 t67: substring population admitted clean closeouts (e.g. sidecar prose
# naming section2_synthesized). Disabled until operator re-enables after fix.
RELAY_TRUST_SYNTHESIZED_GATE_ENABLED = False


def _closeout_source_from_turn(body: str) -> str | None:
    """Return structured closeout_source from the meta: JSON line, if present."""
    match = _META_LINE_RE.search(body or "")
    if match is None:
        return None
    try:
        meta = json.loads(match.group(1).strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    source = meta.get("closeout_source")
    return source if isinstance(source, str) else None


def enforce_synthesized_partial(status: str, *, closeout_source: str) -> str:
    """Synthesized §2 is never disposition-complete — force partial."""
    if closeout_source == _SYNTHESIZED_SOURCE:
        return "partial"
    return status


def parse_synthesized_ack(body: str) -> str | None:
    """Return dispatch_id when operator acks a synthesized closeout."""
    match = _ACK_RE.search(body or "")
    if match is None:
        return None
    return match.group(1).strip()


def _turn_number(turn: dict[str, Any]) -> int:
    try:
        return int(turn.get("turn_number") or 0)
    except (TypeError, ValueError):
        return 0


def pending_synthesized_closeout(
    turns: list[dict[str, Any]],
    *,
    operator_from: str,
) -> str | None:
    """Return dispatch_id blocking the next DIRECTIVE, or None when clear."""
    if not RELAY_TRUST_SYNTHESIZED_GATE_ENABLED:
        return None
    ordered = sorted(turns, key=_turn_number)
    acked: set[str] = set()
    synthesized: list[tuple[int, str]] = []
    for turn in ordered:
        body = str(turn.get("body") or "")
        if turn.get("from") == operator_from:
            for match in _ACK_RE.finditer(body):
                acked.add(match.group(1).strip())
        if turn.get("from") != "cursor-auto":
            continue
        subject = str(turn.get("subject") or "")
        if not subject.startswith("status:done"):
            continue
        if "TYPE: CLOSEOUT" not in body:
            continue
        if _closeout_source_from_turn(body) != _SYNTHESIZED_SOURCE:
            continue
        dispatch_match = re.search(r"(?im)^dispatch_id:\s*(\S+)", body)
        if dispatch_match:
            synthesized.append((_turn_number(turn), dispatch_match.group(1)))
    for _turn_num, dispatch_id in reversed(synthesized):
        if dispatch_id not in acked:
            return dispatch_id
    return None


__all__ = [
    "RELAY_TRUST_SYNTHESIZED_GATE_ENABLED",
    "enforce_synthesized_partial",
    "parse_synthesized_ack",
    "pending_synthesized_closeout",
]
