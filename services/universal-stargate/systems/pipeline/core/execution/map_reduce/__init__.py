"""
Map/Reduce execution for dynamic parallelism.

Provides concurrent map execution with partial success support:
- MapExecutor: Orchestrates fan-out execution
- MapOutputCollection: Enables wildcard access (step.*.field)
- MapIterationContext: Iteration metadata (mapNs.iteration.*)
"""

from .collection import MapIterationContext, MapJsonAccessor, MapOutputCollection
from .executor import MapExecutor

__all__ = [
    "MapExecutor",
    "MapOutputCollection",
    "MapIterationContext",
    "MapJsonAccessor",
]
