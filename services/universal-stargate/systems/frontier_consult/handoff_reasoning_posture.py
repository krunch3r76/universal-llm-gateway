"""Judgment-skill gate for MCP-seat handoff enrich.

Same skip/freeform sets as GIW ``resolve_prompt_preamble``: consult
handoffs get judgment Use-lines; ``none`` is freeform; mechanical/quick skip.
"""

from __future__ import annotations

from implement_admission.admission_read import frontmatter_value
from reasoning_posture_contracts import (
    FREEFORM_CONTRACTS,
    HYPOTHESIZE_SIMULATE_CONTRACTS,
    REASONING_POSTURE_SKIP_CONTRACTS,
)

REASONING_POSTURE_SLUG = "reasoning-posture"
ULG_FOR_LLMS_SLUG = "ulg-for-llms"
HYPOTHESIZE_SIMULATE_SLUG = "hypothesize-simulate"

# Shared with GIW ``cursor_sdk_packet._REASONING_POSTURE_SKIP_CONTRACTS``.
REASONING_POSTURE_SKIP_CONTRACTS = REASONING_POSTURE_SKIP_CONTRACTS
HYPOTHESIZE_SIMULATE_CONTRACTS = HYPOTHESIZE_SIMULATE_CONTRACTS


def handoff_wants_reasoning_posture(text: str, handoff_contract: str | None) -> bool:
    """Return True when this handoff is a judgment contract, not mechanical.

    Uses the derived *handoff_contract* when the route passed one; otherwise
    packet frontmatter. Missing contract does not inject — implement packets
    often omit ``contract:`` and must stay on the mechanical skip path.
    """
    raw = (handoff_contract or frontmatter_value(text, "contract") or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    if lowered in FREEFORM_CONTRACTS:
        return False
    return lowered not in REASONING_POSTURE_SKIP_CONTRACTS


def handoff_wants_hypothesize_simulate(text: str, handoff_contract: str | None) -> bool:
    """Return True when this handoff leaves the option space open to the seat.

    ``consult`` qualifies: a consult carries a pinned Question and may need
    rival generation. ``none`` is freeform — caller prompt is sole authority.
    """
    raw = (handoff_contract or frontmatter_value(text, "contract") or "").strip()
    lowered = raw.lower()
    if lowered in FREEFORM_CONTRACTS:
        return False
    return lowered in HYPOTHESIZE_SIMULATE_CONTRACTS
