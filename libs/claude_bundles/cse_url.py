"""Canonical URL normalization and Claude seat-address parsing (unified chat + Cowork CSE)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

ClaudeSeatKind = Literal["unified_chat", "cowork_cse", "code_session"]

_CHAT_UUID_RE = re.compile(
    r"^/chat/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/?$",
    re.IGNORECASE,
)
_COWORK_CSE_RE = re.compile(r"^/cowork/(cse_[A-Za-z0-9_]+)/?$")
_CODE_SESSION_RE = re.compile(r"^/code/(session_[A-Za-z0-9]+)/?$")
_BARE_CSE_TOKEN = re.compile(r"^(cse_[A-Za-z0-9_]+)/?$")

BINDABLE_SEAT_KINDS = frozenset({"unified_chat", "cowork_cse"})


@dataclass(frozen=True, slots=True)
class ClaudeSeatAddress:
    canonical_url: str
    kind: ClaudeSeatKind
    primary_id: str
    linked_cse_token: str | None = None


def normalize_cse_url(url: str) -> str:
    """Return a stable CSE URL key without fragments or trailing path slashes."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    path = parts.path.rstrip("/") or parts.path
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _claude_host(netloc: str) -> bool:
    host = (netloc or "").lower()
    return host in {"claude.ai", "www.claude.ai"}


def _canonical_claude_url(scheme: str, path: str) -> str:
    path = path.rstrip("/") or path
    return urlunsplit((scheme, "claude.ai", path, "", ""))


def parse_claude_seat_address(raw: str | None) -> ClaudeSeatAddress | None:
    """Parse a Claude.ai seat URL or bare ``cse_…`` token; refuse non-https Claude hosts."""
    text = (raw or "").strip()
    if not text:
        return None

    bare = _BARE_CSE_TOKEN.match(text)
    if bare:
        token = bare.group(1)
        return ClaudeSeatAddress(
            canonical_url=f"https://claude.ai/cowork/{token}",
            kind="cowork_cse",
            primary_id=token,
        )

    parts = urlsplit(text)
    if not parts.scheme or not parts.netloc:
        return None
    if not _claude_host(parts.netloc):
        return None
    if parts.scheme.lower() != "https":
        return None

    path_only = (parts.path or "").rstrip("/")
    if not path_only:
        return None

    chat = _CHAT_UUID_RE.match(path_only)
    if chat:
        uuid_lower = chat.group(1).lower()
        canon = _canonical_claude_url("https", f"/chat/{uuid_lower}")
        return ClaudeSeatAddress(
            canonical_url=canon,
            kind="unified_chat",
            primary_id=uuid_lower,
        )

    cowork = _COWORK_CSE_RE.match(path_only)
    if cowork:
        token = cowork.group(1)
        canon = _canonical_claude_url("https", f"/cowork/{token}")
        return ClaudeSeatAddress(
            canonical_url=canon,
            kind="cowork_cse",
            primary_id=token,
        )

    code = _CODE_SESSION_RE.match(path_only)
    if code:
        session_id = code.group(1)
        canon = _canonical_claude_url("https", f"/code/{session_id}")
        return ClaudeSeatAddress(
            canonical_url=canon,
            kind="code_session",
            primary_id=session_id,
        )

    return None


def is_bindable_claude_seat_url(raw: str | None) -> bool:
    """True when *raw* is a unified Cowork chat or CSE seat address."""
    parsed = parse_claude_seat_address(raw)
    return parsed is not None and parsed.kind in BINDABLE_SEAT_KINDS


def canonical_claude_seat_url(raw: str | None) -> str | None:
    """Return D1 ``canonical_url`` for bindable seat kinds, else None."""
    parsed = parse_claude_seat_address(raw)
    if parsed is None or parsed.kind not in BINDABLE_SEAT_KINDS:
        return None
    return parsed.canonical_url


def claude_seat_identity_key(raw: str | None) -> str:
    """Stable key for comparing seat URLs (page, stored row, request) on both sides."""
    text = (raw or "").strip()
    if not text:
        return ""
    bound = canonical_claude_seat_url(text)
    if bound:
        return bound
    return normalize_cse_url(text)


__all__ = [
    "BINDABLE_SEAT_KINDS",
    "ClaudeSeatAddress",
    "ClaudeSeatKind",
    "canonical_claude_seat_url",
    "claude_seat_identity_key",
    "is_bindable_claude_seat_url",
    "normalize_cse_url",
    "parse_claude_seat_address",
]
