"""Q2 away G3→G5 score-ratify exit detection and validation."""

from __future__ import annotations

import re

from claude_bundles.conductor_stop import (
    parse_stop_tokens,
    validate_score_ratify_packet,
)

Q2_SCORE_RATIFY_MISSING = "q2_score_ratify_missing"

_G3_DONE_RE = re.compile(r"(?im)^\|\s*G3\s*\|[^|]*\|\s*DONE\b")
_GATE_ROW_RE = re.compile(
    r"(?im)^(?:resume_at|entry_gate|persisted_row):\s*(G[456])\b"
)
_SUMMON_MODE_RE = re.compile(
    r"(?i)summon_mode:\s*(attended|confer[_-]and[_-]finish)\b"
)
_PACKET_KIND_RE = re.compile(r"(?im)^packet_kind:\s*(\S+)")


def _extract_summon_mode(text: str | None) -> str | None:
    """Return normalized summon_mode from packet text, or None when absent."""
    if not text:
        return None
    match = _SUMMON_MODE_RE.search(text)
    if not match:
        return None
    return match.group(1).lower().replace("-", "_")


def _is_conductor_packet(
    packet_text: str | None,
    *,
    packet_kind: str | None = None,
) -> bool:
    """True when packet is a conductor mission."""
    if packet_kind == "conductor":
        return True
    if packet_text:
        match = _PACKET_KIND_RE.search(packet_text)
        if match and match.group(1).strip().lower() == "conductor":
            return True
    return False


def is_g3_g5_exit(body: str) -> bool:
    """True when closeout exits G3 toward G5 without an explicit G3 see-score pin."""
    text = body or ""
    parsed = parse_stop_tokens(text)
    if "ROW_PINNED" in parsed.rows.get("G3", frozenset()):
        return False
    if _G3_DONE_RE.search(text):
        return True
    if _GATE_ROW_RE.search(text):
        return True
    return False


def validate_q2_away_score_ratify(
    body: str,
    *,
    packet_text: str | None = None,
    packet_kind: str | None = None,
) -> str | None:
    """Return ``q2_score_ratify_missing`` when away G3→G5 lacks score-ratify posture."""
    if not _is_conductor_packet(packet_text, packet_kind=packet_kind):
        return None
    if _extract_summon_mode(packet_text) == "attended":
        return None
    if not is_g3_g5_exit(body):
        return None
    if validate_score_ratify_packet(body).ok:
        return None
    return Q2_SCORE_RATIFY_MISSING
