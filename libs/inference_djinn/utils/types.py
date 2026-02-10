"""
Shared type definitions for inference_djinn.

This module contains common data structures used across all engines.
"""

from dataclasses import dataclass


@dataclass
class TokenCountResult:
    """Result of token counting operation."""

    tokens: int
    method: str
    success: bool
    error: str | None = None
    time_taken: float | None = None
    formatted_prompt: str | None = None


class TokenCountError(Exception):
    """Raised when token counting fails and cannot be recovered."""

    pass
