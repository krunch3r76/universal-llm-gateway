"""Shared skip-set for reasoning-posture injection on cursor-sdk dispatches.

GIW ``resolve_prompt_preamble`` and Stargate handoff enrich both consult this
frozenset so mechanical contracts skip the posture Use-line while judgment
contracts receive it.
"""

from __future__ import annotations

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('git_integration_worker', 'stargate')

REASONING_POSTURE_SKIP_CONTRACTS = frozenset(
    {"implement", "pure-mechanical", "propagate", "execute", "answer", "ask"}
)

__all__ = ["REASONING_POSTURE_SKIP_CONTRACTS"]
