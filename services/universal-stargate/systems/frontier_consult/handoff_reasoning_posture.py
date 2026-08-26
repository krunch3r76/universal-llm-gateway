"""Judgment-skill gate for MCP-seat handoff enrich.

Same skip set as GIW ``resolve_prompt_preamble``: consult / light-bounded
handoffs get a ``reasoning-posture`` Use-line; mechanical/quick skip.
"""

from __future__ import annotations

from implement_admission.admission_read import frontmatter_value
from reasoning_posture_contracts import REASONING_POSTURE_SKIP_CONTRACTS

REASONING_POSTURE_SLUG = "reasoning-posture"
ULG_FOR_LLMS_SLUG = "ulg-for-llms"

# Shared with GIW ``cursor_sdk_packet._REASONING_POSTURE_SKIP_CONTRACTS``.
REASONING_POSTURE_SKIP_CONTRACTS = REASONING_POSTURE_SKIP_CONTRACTS


def handoff_wants_reasoning_posture(text: str, handoff_contract: str | None) -> bool:
    """Return True when this handoff is a judgment contract, not mechanical.

    Uses the derived *handoff_contract* when the route passed one; otherwise
    packet frontmatter. Missing contract does not inject — implement packets
    often omit ``contract:`` and must stay on the mechanical skip path.
    """
    raw = (handoff_contract or frontmatter_value(text, "contract") or "").strip()
    if not raw:
        return False
    return raw.lower() not in REASONING_POSTURE_SKIP_CONTRACTS
