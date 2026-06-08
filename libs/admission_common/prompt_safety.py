"""Shared prompt token guards for admission surfaces."""

from __future__ import annotations

from collections.abc import Sequence


def forbidden_token_reason(prompt: str, tokens: Sequence[str]) -> str | None:
    """Return a rejection reason if prompt contains a forbidden token, else None."""
    for token in tokens:
        if token in prompt:
            return f"prompt must not contain forbidden argv token {token!r}"
    return None
