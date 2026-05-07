"""Per-entity-type card adapters (Cortex v2.4 §6.4).

Registry: entity-type string → adapter instance. Unknown types resolve
to ``DefaultAdapter``.
"""

from __future__ import annotations

from .base import BaseCardAdapter, CardAdapterCounts
from .case import CaseAdapter
from .decision import DecisionAdapter
from .default import DefaultAdapter
from .document import DocumentAdapter
from .person import PersonAdapter
from .service import ServiceAdapter
from .todo import TodoAdapter

_REGISTRY: dict[str, BaseCardAdapter] = {
    "todo": TodoAdapter(),
    "decision": DecisionAdapter(),
    "document": DocumentAdapter(),
    "service": ServiceAdapter(),
    "case": CaseAdapter(),
    "person": PersonAdapter(),
}

_DEFAULT_ADAPTER: BaseCardAdapter = DefaultAdapter()


def get_adapter(entity_type: str) -> BaseCardAdapter:
    """Resolve *entity_type* to its adapter; unknown types → DefaultAdapter."""
    return _REGISTRY.get(entity_type, _DEFAULT_ADAPTER)


__all__ = [
    "BaseCardAdapter",
    "CardAdapterCounts",
    "CaseAdapter",
    "DecisionAdapter",
    "DefaultAdapter",
    "DocumentAdapter",
    "PersonAdapter",
    "ServiceAdapter",
    "TodoAdapter",
    "get_adapter",
]
