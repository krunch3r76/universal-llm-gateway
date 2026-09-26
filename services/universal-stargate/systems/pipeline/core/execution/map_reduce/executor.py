"""Compatibility wrapper for the legacy `MapExecutor` import path in map_reduce.

Re-exports `MapExecutor` from the `map_executor` package, where the fan-out
orchestration for pipeline map steps now lives. Contains no logic; new code should
import from `map_reduce` or `map_reduce.map_executor` directly.
"""

from .map_executor import MapExecutor

__all__ = ["MapExecutor"]
