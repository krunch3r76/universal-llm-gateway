"""Digest extract backend selector — sync Stargate vs async CDP."""

from __future__ import annotations

import os
from typing import Any, Literal

from .claim_batch_verify import model_family

ExtractBackend = Literal["stargate", "cdp"]

_VALID_BACKENDS = frozenset({"stargate", "cdp"})


def extract_backend() -> ExtractBackend:
    raw = os.environ.get("CORTEX_DIGEST_EXTRACT_BACKEND", "stargate").strip().lower()
    if raw not in _VALID_BACKENDS:
        return "stargate"
    return raw  # type: ignore[return-value]


def validate_digest_backend_config() -> str | None:
    """Return an actionable error when backend/model pairing violates binds."""
    backend = extract_backend()
    verify_model = os.environ.get("CORTEX_DIGEST_VERIFY_MODEL", "").strip()
    extract_model = os.environ.get("CORTEX_DIGEST_EXTRACT_MODEL", "").strip()

    if backend == "cdp":
        if model_family(verify_model) == "anthropic":
            return (
                "CORTEX_DIGEST_EXTRACT_BACKEND=cdp requires a non-anthropic "
                "CORTEX_DIGEST_VERIFY_MODEL (cross-family invariant)."
            )
        if not os.environ.get("CORTEX_DIGEST_CDP_PROJECT_UUID", "").strip():
            return (
                "CORTEX_DIGEST_CDP_PROJECT_UUID is required when "
                "CORTEX_DIGEST_EXTRACT_BACKEND=cdp."
            )
    elif backend == "stargate" and extract_model.lower().startswith("anthropic/"):
        return (
            "CORTEX_DIGEST_EXTRACT_BACKEND=stargate refuses anthropic/* "
            "CORTEX_DIGEST_EXTRACT_MODEL (substrate bind)."
        )
    return None


def extract_claims(
    entry_text: str,
    *,
    entry_anchor: str,
    journal_uri: str,
) -> dict[str, Any] | None:
    """Route extract to the configured backend."""
    config_error = validate_digest_backend_config()
    if config_error:
        raise RuntimeError(config_error)

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
