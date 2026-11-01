"""Grant gate for mutating web-tab actuation (aligns with paste_as_operator / chat_session)."""

from __future__ import annotations

from typing import Literal

GrantKind = Literal["explicit", "operator"]

_ALLOWED: frozenset[str] = frozenset({"explicit", "operator"})


def require_grant(grant: str | None) -> dict[str, object] | None:
    """Return refusal dict when *grant* is missing or invalid; else ``None``."""
    g = (grant or "").strip()
    if g not in _ALLOWED:
        return {
            "ok": False,
            "code": "grant_required",
            "reason": "grant must be explicit or operator",
        }
    return None


def require_write_authorization(
    grant: str | None,
    authorization_source: str | None,
) -> dict[str, object] | None:
    """Grant gate plus ``authorization_source`` (paste_as_operator parity)."""
    refused = require_grant(grant)
    if refused:
        return refused
    g = (grant or "").strip()
    auth = (authorization_source or "").strip()
    if not auth:
        return {
            "ok": False,
            "code": "grant_or_source_required",
            "reason": "grant and authorization_source are required",
            "grant": g or None,
            "authorization_source": None,
        }
    return None
