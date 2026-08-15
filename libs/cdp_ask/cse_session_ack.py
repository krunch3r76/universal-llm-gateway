"""Typed ACK classifier over harvested CSE text — prose convention only."""

from __future__ import annotations

import re
from typing import Literal

AckClass = Literal["typed_ack", "ordinary_content", "no_proof"]

_TYPED_ACK_RE = re.compile(
    r"TYPE:\s*(?:SEAT_STAND_DOWN_ACK|SUCCESSOR_ATTESTATION)\b",
    re.IGNORECASE,
)


def classify_ack(
    text: str,
    *,
    marker: str | None = None,
    successor_birth_id: str | None = None,
) -> AckClass:
    """Return ``typed_ack`` only when grammar and marker/successor_birth_id match."""
    body = (text or "").strip()
    if not body:
        return "no_proof"
    if not _TYPED_ACK_RE.search(body):
        return "ordinary_content"
    marker_hit = bool(marker and marker in body)
    birth_hit = bool(successor_birth_id and successor_birth_id in body)
    if marker_hit or birth_hit:
        return "typed_ack"
    return "ordinary_content"
