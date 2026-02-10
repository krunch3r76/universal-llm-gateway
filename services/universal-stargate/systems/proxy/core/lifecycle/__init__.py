"""Lifecycle event emission for request processing."""

from .execution_completed import emit_execution_completed

__all__ = ["emit_execution_completed"]
