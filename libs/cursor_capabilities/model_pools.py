"""Cursor SDK usage-pool membership (primary key vs Other Models)."""

from __future__ import annotations

from typing import Final

from .cursor_capabilities import CURSOR_MODEL_CAPABILITIES, canonical_cursor_bare_id

# Primary API key (``CURSOR_API_KEY`` / ``auth.json``). Not the billing label:
# cursor.com "Cursor Models" is Composer plus Grok 4.7/4.6/4.5, and Auto is
# absent from that row. Auto stays here because it is the router, not a
# third-party id. Uncarded Grok ids are listed so a later card does not fall
# onto ``CURSOR_API_KEY_OTHER_MODELS``.
CURSOR_MODELS_POOL_BARE: Final[frozenset[str]] = frozenset(
    {
        "composer-2.5",
        "grok-4.7",
        "grok-4.6",
        "grok-4.5",
        # ListModels id ``default``; card id ``auto`` — Cursor Router / Auto tier.
        "auto",
    }
)

# Billing name "Cursor Models" on cursor.com/docs/models-and-pricing. Auto is
# on the primary key and is not a member of this label.
CURSOR_MODELS_BILLING_BARE: Final[frozenset[str]] = frozenset(
    {
        "composer-2.5",
        "grok-4.7",
        "grok-4.6",
        "grok-4.5",
    }
)

OTHER_MODELS_BARE: Final[frozenset[str]] = frozenset(
    model_id
    for model_id in CURSOR_MODEL_CAPABILITIES
    if model_id not in CURSOR_MODELS_POOL_BARE
)


def is_other_models_pool(model_id: str | None) -> bool:
    """True when *model_id* draws Cursor's capped Other Models (second) pool.

    Only carded cursor-sdk models are classified; unknown bare ids return False
    (incumbent GIW contract — do not let ``canonical_cursor_bare_id`` ValueError
    escape).
    """
    try:
        bare = canonical_cursor_bare_id(str(model_id or ""))
    except ValueError:
        return False
    if bare not in CURSOR_MODEL_CAPABILITIES:
        return False
    return bare not in CURSOR_MODELS_POOL_BARE


__all__ = [
    "CURSOR_MODELS_BILLING_BARE",
    "CURSOR_MODELS_POOL_BARE",
    "OTHER_MODELS_BARE",
    "is_other_models_pool",
]
