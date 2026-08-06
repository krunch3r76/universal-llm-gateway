"""Post-time wire normalize for claim-bearing terminal payloads.

NAMED ABSENCE (row 29 / Packet A — read this before extending):
This module only covers claim-bearing keys that transit
``post_terminal_status`` (currently ``fix_hint``). It does **not** cover:

- member 2 — ledger ``fail_row`` / status column (Packet D)
- member 5 — ``Verification`` schema packing outside this chokepoint (Packet C)
- member 6 — authoring / skill / mission-close surfaces (Packet E)

Do not claim a seventh arbitrary site is covered. Extend the key set when a
family that *does* post through this chokepoint is retrofitted; leave other
families to their packets.

Post-time policy (operator bind, Packet A): **never fail-closed at POST**.
A guard that refuses to post a claim-bearing terminal destroys the closeout
(relay-death / ``failed_on_restart`` family). Degrade visibly: stamp
``CLAIM_REGISTER_UNKNOWN``, keep posting, log loud. Fail-closed lives at
``Claimed`` construction and in unit tests only.
"""

from __future__ import annotations

import logging
from typing import Any

from claim_register.types import (
    CLAIM_REGISTER_UNKNOWN,
    Claimed,
    _VALID_REGISTERS,
)

logger = logging.getLogger(__name__)

# Claim-bearing keys in cursor-auto terminal JSON that require a register.
# Expand only when a retrofit actually emits through post_terminal_status.
CLAIM_BEARING_KEYS: frozenset[str] = frozenset({"fix_hint"})

_DEGRADE_BASIS = "post_terminal_status_untyped_claim"


def normalize_claim_bearing_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Stamp missing registers on claim-bearing keys; never raise to block post.

    Returns a shallow-copied payload when any key is rewritten so callers can
    keep the original dict if needed. Already-typed ``Claimed`` wire shapes
    (``register`` ∈ observed|derived) pass through unchanged.
    """
    dirty = False
    out = payload
    for key in CLAIM_BEARING_KEYS:
        if key not in payload:
            continue
        normalized = _normalize_claim_value(payload[key], key=key)
        if normalized is not payload[key]:
            if not dirty:
                out = dict(payload)
                dirty = True
            out[key] = normalized
    return out


def _normalize_claim_value(raw: Any, *, key: str) -> Any:
    if isinstance(raw, Claimed):
        return raw.to_wire()
    if isinstance(raw, dict) and "register" in raw and "value" in raw:
        reg = raw["register"]
        if reg in _VALID_REGISTERS or reg == CLAIM_REGISTER_UNKNOWN:
            return raw
        # Invalid register token — stamp unknown, keep value.
        logger.error(
            "claim_register post degrade: key=%s had invalid register=%r; "
            "stamping %s (post never fail-closed)",
            key,
            reg,
            CLAIM_REGISTER_UNKNOWN,
        )
        return {
            "register": CLAIM_REGISTER_UNKNOWN,
            "value": raw.get("value", raw),
            "basis": _DEGRADE_BASIS,
        }
    # Bare string / untyped value — the soft-fix shape.
    logger.error(
        "claim_register post degrade: key=%s arrived without register; "
        "stamping %s and posting anyway (named partial guard — not members 2/5/6)",
        key,
        CLAIM_REGISTER_UNKNOWN,
    )
    return {
        "register": CLAIM_REGISTER_UNKNOWN,
        "value": raw,
        "basis": _DEGRADE_BASIS,
    }
