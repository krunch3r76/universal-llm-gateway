"""
Map iteration context and compatibility exports.

`MapOutputCollection` and `MapJsonAccessor` live in `map_output_collection.py`.
They are re-exported here to preserve import compatibility.
"""

from dataclasses import dataclass
from typing import Any

from .map_output_collection import MapJsonAccessor, MapOutputCollection


@dataclass(frozen=True, slots=True)
class MapIterationContext:
    """Iteration context accessed via mapNs.iteration.*"""

    index: int
    value: Any
    key: str | None
    total: int

__all__ = ["MapOutputCollection", "MapJsonAccessor", "MapIterationContext"]
