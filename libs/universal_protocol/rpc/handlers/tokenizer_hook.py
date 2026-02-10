"""Tokenizer integration hook for RPC handlers.

Single responsibility: Register and retrieve tokenizer callback.
"""

from collections.abc import Callable
from typing import Any

# Tokenizer callback storage
_tokenizer_callback: Callable[[str, str | None], Any] | None = None


def register_tokenizer_callback(callback: Callable[[str, str | None], Any]) -> None:
    """Register a tokenizer callback for exact token counting.

    The callback should be an async function with signature:
        async def tokenizer(text: str, model: str | None) -> dict

    It should return {"count": int} with the exact token count.
    """
    global _tokenizer_callback
    _tokenizer_callback = callback


def get_tokenizer_callback() -> Callable[[str, str | None], Any] | None:
    """Get registered tokenizer callback (may be None)."""
    return _tokenizer_callback

