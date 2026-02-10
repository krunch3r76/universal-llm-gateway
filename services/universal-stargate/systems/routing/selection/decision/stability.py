"""
Sticky placement tracking for routing stability.

Provides hysteresis to prevent gateway oscillation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from model_id import ModelId


@dataclass(slots=True)
class StickyPlacementTracker:
    """
    Tracks model→gateway bindings for routing stability.

    Invariant: ∀ model_id ∈ _bindings: binding reflects last successful selection
    Lifecycle: Owned by StargateProxy, lives for process lifetime

    Thread-safety: Not thread-safe. Designed for single-threaded async context.
    For multi-worker: Consider shared state (Redis, etc.) in future phase.
    """

    _bindings: dict[ModelId, str] = field(default_factory=dict)

    def get_current_best(self, model_id: ModelId) -> str | None:
        """
        Get current binding for model.

        Returns:
            Gateway name if model has been routed before, None otherwise
        """
        return self._bindings.get(model_id)

    def update_binding(self, model_id: ModelId, gateway_name: str) -> None:
        """
        Record successful gateway selection.

        Called after DecisionEngine.select() returns a gateway.
        Provides stability bonus on subsequent requests.
        """
        self._bindings[model_id] = gateway_name

    def clear_binding(self, model_id: ModelId) -> None:
        """
        Clear binding for a model.

        Call when model is unloaded from its bound gateway,
        forcing fresh selection on next request.
        """
        self._bindings.pop(model_id, None)

    def clear_all(self) -> None:
        """
        Clear all bindings.

        Call on topology changes (gateway added/removed)
        to force fresh evaluation.
        """
        self._bindings.clear()

    @property
    def binding_count(self) -> int:
        """Number of tracked bindings (for observability)."""
        return len(self._bindings)
