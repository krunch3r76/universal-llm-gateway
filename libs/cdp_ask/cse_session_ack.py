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


MarkerType = Literal["stand_down_ack", "successor_attestation"]

STAND_DOWN_ACK_TYPE_RE = re.compile(r"TYPE:\s*SEAT_STAND_DOWN_ACK\b", re.IGNORECASE)
SUCCESSOR_ATTESTATION_TYPE_RE = re.compile(
    r"TYPE:\s*SUCCESSOR_ATTESTATION\b", re.IGNORECASE
)


def marker_type(text: str) -> MarkerType | None:
    """Return which typed-ACK TYPE token *text* carries, independent of replay nonce.

    Callers: hop-cadence stand-down probe (historical bus-turn scan). Does not
    consult marker/successor_birth_id. Empty or ordinary prose → None. Bare
    ``TYPE: SEAT_STAND_DOWN`` (non-ACK) → None. When both tokens appear in one
    body, the later match span wins (intra-turn latest-typed-marker-wins).
    """
    body = (text or "").strip()
    if not body:
        return None
    stand_down = STAND_DOWN_ACK_TYPE_RE.search(body)
    successor = SUCCESSOR_ATTESTATION_TYPE_RE.search(body)
    if stand_down is None and successor is None:
        return None
    if stand_down is None:
        return "successor_attestation"
    if successor is None:
        return "stand_down_ack"
    if stand_down.start() >= successor.start():
        return "stand_down_ack"
    return "successor_attestation"
