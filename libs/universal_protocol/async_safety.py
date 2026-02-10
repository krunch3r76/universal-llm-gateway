"""Async safety verification for development and testing.

Provides decorators that verify functions are called from the expected
async context. Zero overhead in production (decorator is no-op).

Usage:
    @require_async_context
    def set_model_busy(self, model_id: str):
        # Raises RuntimeError if called outside async context (dev only)
        ...
"""

import asyncio
import functools
import os
from collections.abc import Callable
from typing import Any

# Enable via environment variable (default: disabled for zero overhead)
VERIFY_ASYNC_SAFETY = os.getenv("VERIFY_ASYNC_SAFETY", "").lower() == "true"


def require_async_context[F: Callable[..., Any]](func: F) -> F:
    """Decorator: Require function to be called from async context.

    In development (VERIFY_ASYNC_SAFETY=true):
        - Raises RuntimeError if called outside async context
        - Raises RuntimeError if called from different event loop

    In production (VERIFY_ASYNC_SAFETY unset/false):
        - No overhead, decorator is no-op
    """
    if not VERIFY_ASYNC_SAFETY:
        return func  # No overhead in production

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        # Verify async context
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise RuntimeError(
                f"{func.__qualname__} must be called from async context! "
                "Use 'await' or run within async function."
            )

        # Verify same event loop (detect cross-thread access)
        if not hasattr(self, "_bound_event_loop"):
            self._bound_event_loop = loop
        elif self._bound_event_loop != loop:
            raise RuntimeError(
                f"{func.__qualname__} called from different event loop! "
                f"This indicates cross-thread access. "
                f"Expected loop {id(self._bound_event_loop)}, got {id(loop)}"
            )

        return func(self, *args, **kwargs)

    return wrapper


def require_single_thread[F: Callable[..., Any]](func: F) -> F:
    """Decorator: Verify function is called from same thread.

    Lighter weight than require_async_context - just checks thread ID.
    Useful for classes that may be used outside async context.
    """
    if not VERIFY_ASYNC_SAFETY:
        return func

    import threading

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if not hasattr(self, "_bound_thread_id"):
            self._bound_thread_id = threading.get_ident()
        elif self._bound_thread_id != threading.get_ident():
            raise RuntimeError(
                f"{func.__qualname__} called from different thread! "
                f"Expected thread {self._bound_thread_id}, "
                f"got {threading.get_ident()}"
            )

        return func(self, *args, **kwargs)

    return wrapper
