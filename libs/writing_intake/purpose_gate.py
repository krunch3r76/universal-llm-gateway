"""Backward-compatible re-exports; prefer claim_gates for new code."""

from __future__ import annotations

from .claim_gates import (
    check_class_for_channel,
    check_profile_for_channel,
    is_declaration_register,
    map_claim_to_purpose,
    parse_purpose_points,
    purpose_content_tokens,
)

__all__ = [
    "check_class_for_channel",
    "check_profile_for_channel",
    "is_declaration_register",
    "map_claim_to_purpose",
    "parse_purpose_points",
    "purpose_content_tokens",
]
