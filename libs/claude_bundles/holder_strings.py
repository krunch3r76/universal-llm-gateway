"""Single formatter/parser for ``cowork_cse:<holder_id>`` and ``cse:<holder_id>`` nest wire."""

from __future__ import annotations

import re

from claude_bundles.cse_identity_drift import cse_token_from_url

COWORK_CSE_PREFIX = "cowork_cse:"
NEST_CSE_PREFIX = "cse:"

# SDK dispatch ids are hex-ish slugs; pure digit strings are bus_watch epoch tokens.
_SDK_EPOCH_RE = re.compile(r"^\d{10,}$")


def holder_id_from_chat_url(chat_url: str) -> str | None:
    """Return ``cse_<id>`` parsed from a normalized Cowork session URL."""
    return cse_token_from_url(chat_url)


def format_cowork_cse_holder(holder_id: str) -> str:
    """Emit the multi-path holder string ``cowork_cse:<holder_id>``."""
    hid = (holder_id or "").strip()
    if not hid:
        raise ValueError("holder_id required")
    return f"{COWORK_CSE_PREFIX}{hid}"


def parse_cowork_cse_holder(raw: str | None) -> str | None:
    """Parse ``cowork_cse:<holder_id>``; return None for non-CSE strings."""
    text = (raw or "").strip()
    if not text.startswith(COWORK_CSE_PREFIX):
        return None
    holder_id = text[len(COWORK_CSE_PREFIX) :].strip()
    if not holder_id or not holder_id.startswith("cse_"):
        return None
    return holder_id


def format_nest_under_cse(holder_id: str) -> str:
    """Emit typed nest parent wire value ``cse:<holder_id>``."""
    hid = (holder_id or "").strip()
    if not hid:
        raise ValueError("holder_id required")
    return f"{NEST_CSE_PREFIX}{hid}"


def parse_nest_under_cse(raw: str | None) -> str | None:
    """Parse ``cse:<holder_id>`` from ``nest_under``; never classify SDK epochs."""
    text = (raw or "").strip()
    if not text.startswith(NEST_CSE_PREFIX):
        return None
    holder_id = text[len(NEST_CSE_PREFIX) :].strip()
    if not holder_id or _SDK_EPOCH_RE.match(holder_id):
        return None
    if not holder_id.startswith("cse_"):
        return None
    return holder_id


def is_cse_holder_string(raw: str | None) -> bool:
    """True when *raw* is a CSE holder string (cowork_cse or cse nest prefix)."""
    return parse_cowork_cse_holder(raw) is not None or parse_nest_under_cse(raw) is not None


__all__ = [
    "COWORK_CSE_PREFIX",
    "NEST_CSE_PREFIX",
    "format_cowork_cse_holder",
    "format_nest_under_cse",
    "holder_id_from_chat_url",
    "is_cse_holder_string",
    "parse_cowork_cse_holder",
    "parse_nest_under_cse",
]
