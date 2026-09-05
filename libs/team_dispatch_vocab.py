"""Shared ``team_dispatch`` contract vocabulary for MCP + Stargate generate wire.

Distinct from ``contract_vocab.CANONICAL_CONTRACTS`` (``agent_bus.request``).
"""

from __future__ import annotations

from typing import Literal

TEAM_DISPATCH_CONTRACTS: frozenset[str] = frozenset(
    {
        "sketch",
        "implement",
        "wrap",
        "conductor",
        "pure-mechanical",
        "none",
    }
)

TeamDispatchContract = Literal[
    "sketch",
    "implement",
    "wrap",
    "conductor",
    "pure-mechanical",
    "none",
]

MATERIALIZER_CONTRACTS: frozenset[str] = frozenset(
    {"sketch", "implement", "wrap", "conductor"}
)
RESIDUAL_CONTRACTS: frozenset[str] = frozenset({"none", "pure-mechanical"})

TO_THREAD_CONTRACTS: frozenset[str] = frozenset(
    {"sketch", "implement", "pure-mechanical", "none"}
)
HANDOFF_OVERRIDE_CONTRACTS: frozenset[str] = frozenset(
    {"sketch", "pure-mechanical", "implement", "none"}
)

__all__ = [
    "HANDOFF_OVERRIDE_CONTRACTS",
    "MATERIALIZER_CONTRACTS",
    "RESIDUAL_CONTRACTS",
    "TEAM_DISPATCH_CONTRACTS",
    "TO_THREAD_CONTRACTS",
    "TeamDispatchContract",
]
