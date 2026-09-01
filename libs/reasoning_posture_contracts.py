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

# ``light-bounded`` leaves the option space to the seat, so it needs the rival
# fill more than ``consult``, which arrives with a pinned Question and scope-lock.
HYPOTHESIZE_SIMULATE_CONTRACTS = frozenset({"consult", "light-bounded"})

__all__ = [
    "HYPOTHESIZE_SIMULATE_CONTRACTS",
    "REASONING_POSTURE_SKIP_CONTRACTS",
]
