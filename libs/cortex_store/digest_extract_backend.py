"""Digest extract backend selector — sync Stargate vs async CDP."""

from __future__ import annotations

import os
from typing import Any, Literal

ExtractBackend = Literal["stargate", "cdp"]

_VALID_BACKENDS = frozenset({"stargate", "cdp"})


def extract_backend() -> ExtractBackend:
    raw = os.environ.get("CORTEX_DIGEST_EXTRACT_BACKEND", "stargate").strip().lower()
    if raw not in _VALID_BACKENDS:
        return "stargate"
    return raw  # type: ignore[return-value]


def extract_claims(
    entry_text: str,
    *,
    entry_anchor: str,
    journal_uri: str,
) -> dict[str, Any] | None:
    """Route extract to the configured backend."""
    if extract_backend() == "cdp":
        from .digest_extract_cdp import extract_claims_cdp

        return extract_claims_cdp(
            entry_text,
            entry_anchor=entry_anchor,
            journal_uri=journal_uri,
        )

    from .journal_digest_extract import extract_claims as extract_claims_stargate

    return extract_claims_stargate(
        entry_text,
        entry_anchor=entry_anchor,
        journal_uri=journal_uri,
    )
