"""Shared registration-drift fields for harvest identity echo and provenance conflict."""

from __future__ import annotations

import re
from typing import Any, Literal

from claude_bundles.cse_url import normalize_cse_url

IdentityCheck = Literal[
    "match",
    "registration_drift",
    "foreign_transcript",
    "unverified",
]

_CSE_TOKEN = re.compile(r"(cse_[A-Za-z0-9]+)")
_CSE_INPUT = re.compile(
    r"^(?:https://claude\.ai/(?:cowork/)?)?(cse_[A-Za-z0-9]+)/?$"
)


def _cse_url_from_token(raw: str) -> str | None:
    text = (raw or "").strip()
    match = _CSE_INPUT.match(text)
    if match:
        return normalize_cse_url(f"https://claude.ai/cowork/{match.group(1)}")
    if "/cowork/cse_" in text:
        return normalize_cse_url(text)
    return None


def registration_drift_fields(
    requested_registration_id: str | None,
    current_registration_id: str | None,
) -> dict[str, str | None]:
    """Byte-identical drift keys for harvest identity and provenance conflict."""
    return {
        "requested_registration_id": requested_registration_id,
        "current_registration_id": current_registration_id,
    }


def cse_token_from_url(raw: str | None) -> str | None:
    """Extract the bare ``cse_…`` token from a Cowork URL or token string."""
    normalized = _cse_url_from_token(raw or "")
    if not normalized:
        return None
    match = _CSE_TOKEN.search(normalized)
    return match.group(1) if match else None


def build_harvest_identity_block(
    *,
    requested_chat_url: str | None,
    requested_registration_id: str | None,
    observed_chat_url: str | None,
    current_registration_id: str | None,
    identity_check: IdentityCheck,
) -> dict[str, Any]:
    """Construct the harvest ``identity`` block with shared drift key names."""
    block: dict[str, Any] = {
        "requested_chat_url": requested_chat_url,
        "observed_chat_url": observed_chat_url,
        "identity_check": identity_check,
    }
    block.update(
        registration_drift_fields(
            requested_registration_id,
            current_registration_id,
        )
    )
    return block


def evaluate_harvest_identity(
    *,
    requested_chat_url: str | None,
    requested_registration_id: str | None,
    observed_chat_url: str | None,
    current_registration_id: str | None,
) -> dict[str, Any]:
    """Three-state harvest identity check — absence of evidence is never ``match``."""
    requested_cse = cse_token_from_url(requested_chat_url)
    observed_cse = cse_token_from_url(observed_chat_url)
    if observed_cse is None:
        check: IdentityCheck = "unverified"
    elif requested_cse is not None and observed_cse != requested_cse:
        check = "foreign_transcript"
    elif (
        requested_registration_id
        and current_registration_id
        and requested_registration_id != current_registration_id
    ):
        check = "registration_drift"
    elif requested_cse is not None and observed_cse == requested_cse:
        check = "match"
    else:
        check = "unverified"
    return build_harvest_identity_block(
        requested_chat_url=requested_chat_url,
        requested_registration_id=requested_registration_id,
        observed_chat_url=observed_chat_url,
        current_registration_id=current_registration_id,
        identity_check=check,
    )
