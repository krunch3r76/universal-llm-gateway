"""Shared contract sets for judgment-skill injection on dispatch preambles.

GIW ``resolve_prompt_preamble`` and Stargate handoff enrich both consult these
frozensets so mechanical contracts skip the posture Use-line while judgment
contracts receive it.
"""

from __future__ import annotations

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('git_integration_worker', 'stargate')

REASONING_POSTURE_SKIP_CONTRACTS = frozenset(
    {"implement", "pure-mechanical", "propagate", "execute", "answer", "ask"}
)

# Shared one-liner for GIW preamble, Stargate handoff enrich, and cursor-auto admit.
REASONING_POSTURE_PREAMBLE = (
    "Use the `reasoning-posture` skill — pin Question/OOS/detent before merits; "
    "steelman / calibrate / courage; thinking_off does not waive."
)


def reasoning_posture_warrants_injection(contract: str | None) -> bool:
    """True when *contract* should receive the judgment posture invoke line."""
    return (contract or "").strip().lower() not in REASONING_POSTURE_SKIP_CONTRACTS


# ``light-bounded`` leaves the option space to the seat, so it needs the rival
# fill more than ``consult``, which arrives with a pinned Question and scope-lock.
HYPOTHESIZE_SIMULATE_CONTRACTS = frozenset({"consult", "light-bounded"})

__all__ = [
    "HYPOTHESIZE_SIMULATE_CONTRACTS",
    "REASONING_POSTURE_PREAMBLE",
    "REASONING_POSTURE_SKIP_CONTRACTS",
    "reasoning_posture_warrants_injection",
]
