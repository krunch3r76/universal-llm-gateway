"""Cursor SDK model pool membership — static frozenset SOT."""

from __future__ import annotations

from .cursor_capabilities import canonical_cursor_bare_id

OTHER_MODELS_BARE: frozenset[str] = frozenset(
    {
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-fable-5",
        "claude-fable-5-1",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
        "gpt-5.6-luna",
    }
)


def is_other_models_pool(model_id: str | None) -> bool:
    """True when *model_id* draws Cursor's capped Other Models (second) pool.

    Foreign / unparseable ids return False (incumbent GIW contract — do not
    let ``canonical_cursor_bare_id`` ValueError escape).
    """
    try:
        return canonical_cursor_bare_id(str(model_id or "")) in OTHER_MODELS_BARE
    except ValueError:
        return False


__all__ = ["OTHER_MODELS_BARE", "is_other_models_pool"]
