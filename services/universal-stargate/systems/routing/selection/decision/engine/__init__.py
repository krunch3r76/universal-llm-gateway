"""Package exports for the routing decision engine package-shadow split.

Consumers continue importing `DecisionEngine` and `create_decision_engine` from
`selection.decision.engine` while implementation details live in submodules.
"""

from __future__ import annotations

from .core import DecisionEngine, create_decision_engine

__all__ = ["DecisionEngine", "create_decision_engine"]
