"""
Error builder utilities.

Common patterns and helper functions for error handling.
"""

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException


def raise_if_none(
    value: Any,
    error_builder_func: Callable[..., HTTPException],
    *args,
    **kwargs,
) -> Any:
    """
    Raise error if value is None, otherwise return value.

    Args:
        value: Value to check
        error_builder_func: Function that returns HTTPException
        *args, **kwargs: Arguments to pass to error_builder_func

    Returns:
        value if not None

    Raises:
        HTTPException from error_builder_func if value is None
    """
    if value is None:
        raise error_builder_func(*args, **kwargs)
    return value
