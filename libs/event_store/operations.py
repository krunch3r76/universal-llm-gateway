"""Public facade for event-store named operations.

Consumers import this module for operation discovery and execution. The actual
catalog, parameter coercion, and dispatcher live in split modules to keep each
responsibility small while preserving the historical import path.
"""

from __future__ import annotations

from .operation_catalog import list_operations
from .operation_dispatch import execute_operation

__all__ = ["execute_operation", "list_operations"]
