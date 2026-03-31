"""Provider-specific output token ceilings used for request validation/clamping."""

from __future__ import annotations

from typing import Final

# Ordered most-specific first so dated snapshots win before family aliases.
_ANTHROPIC_MAX_OUTPUT_TOKENS: tuple[tuple[str, int], ...] = (
    ("claude-opus-4-6", 128000),
    ("claude-opus-4.6", 128000),
    ("claude-sonnet-4-6", 64000),
    ("claude-sonnet-4.6", 64000),
    ("claude-haiku-4-5-20251001", 64000),
    ("claude-haiku-4-5", 64000),
    ("claude-haiku-4.5", 64000),
    ("claude-opus-4-5-20251101", 64000),
    ("claude-opus-4-5", 64000),
    ("claude-opus-4.5", 64000),
    ("claude-sonnet-4-5-20250929", 64000),
    ("claude-sonnet-4-5", 64000),
    ("claude-sonnet-4.5", 64000),
    ("claude-opus-4-1-20250805", 32000),
    ("claude-opus-4-1", 32000),
    ("claude-opus-4.1", 32000),
    ("claude-sonnet-4-20250514", 64000),
    ("claude-sonnet-4-0", 64000),
    ("claude-sonnet-4", 64000),
    ("claude-opus-4-20250514", 32000),
    ("claude-opus-4-0", 32000),
    ("claude-opus-4", 32000),
    ("claude-3-5-sonnet", 8192),
    ("claude-3-5-haiku", 8192),
    ("claude-3-opus", 4096),
    ("claude-3-sonnet", 4096),
    ("claude-3-haiku", 4096),
)

_UNKNOWN_ANTHROPIC_MAX_OUTPUT_TOKENS: Final[int] = 8192


def anthropic_max_output_tokens(model: str) -> int:
    """Return the best-known Anthropic max output tokens for ``model``.

    Anthropic rejects ``max_tokens`` values above the per-model ceiling. When a
    model is unknown to our static table, return a conservative fallback so we
    fail closed instead of sending obviously invalid oversized requests.
    """
    normalized = str(model).strip().lower()
    for marker, limit in _ANTHROPIC_MAX_OUTPUT_TOKENS:
        if marker in normalized:
            return limit
    return _UNKNOWN_ANTHROPIC_MAX_OUTPUT_TOKENS


def clamp_anthropic_max_tokens(model: str, requested_max_tokens: int) -> int:
    """Clamp a requested Anthropic ``max_tokens`` value to the model ceiling."""
    return min(requested_max_tokens, anthropic_max_output_tokens(model))
