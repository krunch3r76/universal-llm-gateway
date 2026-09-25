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
_CONTRACT_FRONTMATTER_RE = re.compile(r"(?im)^contract:\s*(\S+)")


def _is_conductor_packet(packet_text: str | None) -> bool:
    """True when packet frontmatter declares ``contract: conductor``."""
    if not packet_text:
        return False
    match = _CONTRACT_FRONTMATTER_RE.search(packet_text)
    return bool(match and match.group(1).strip().lower() == "conductor")


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
) -> str | None:
    """Return ``q2_score_ratify_missing`` when a G3→G5 exit lacks score-ratify posture.

    Attended summon_mode is not an exemption. A live chat is not a human gate.
    """
    if not _is_conductor_packet(packet_text):
        return None
    if not is_g3_g5_exit(body):
        return None
    if validate_score_ratify_packet(body).ok:
        return None
    return Q2_SCORE_RATIFY_MISSING
