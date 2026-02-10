"""
Gateway queue/waiting utilities.

Single responsibility: Event-driven waiting for execution completion.
"""

from .execution_completion_waiter import ExecutionCompletionWaiter

__all__ = ["ExecutionCompletionWaiter"]
